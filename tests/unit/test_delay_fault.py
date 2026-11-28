"""Synthetic time/kernel/packet fixtures only; actual mechanism is lab-tested."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.clock import ClockOrigin
from pvcomm.config.fault_scenes import a02_scenario
from pvcomm.config.faults import FaultScenarioConfig, GlobalScope, RegionScope, StationScope
from pvcomm.network.regional import RegionalNetwork
from pvcomm.qc.delay import audit_delay_operations, match_deliveries, measure_delays
from pvcomm.scenario.delay_fault import DelayFaultController


def configuration() -> FaultScenarioConfig:
    return a02_scenario(StationScope(kind="station", station_ids=("PV01",)), 200, "master_to_rtu")


@pytest.mark.parametrize(
    "change",
    [
        {"additional_delay_ms": 500},
        {"additional_delay_ms": "200"},
        {"additional_delay_ms": 200.0},
        {"direction": "both"},
        {"duration_s": 1},
        {"namespace": "host"},
        {"scope": {"kind": "region", "region_id": "unknown"}},
    ],
)
def test_invalid_delay_configuration(change: dict[str, Any]) -> None:
    raw = configuration().model_dump(mode="json")
    raw["fault"].update(change)
    with pytest.raises(ValueError):
        FaultScenarioConfig.model_validate(raw)


def test_delay_requires_new_version_and_explicit_baseline() -> None:
    raw = configuration().model_dump(mode="json")
    raw["schema_version"] = "0.4-fault.1"
    with pytest.raises(ValueError, match="requires fault contract"):
        FaultScenarioConfig.model_validate(raw)
    raw["schema_version"] = "0.4-fault.2"
    raw["background"]["simulation"]["protocol"]["stations"][0]["access_link"] = None
    with pytest.raises(ValueError, match="explicit NetEm baseline"):
        FaultScenarioConfig.model_validate(raw)


def test_direction_and_scope_resolve_distinct_egresses() -> None:
    network = RegionalNetwork(configuration().background.simulation)
    site = StationScope(kind="station", station_ids=("PV01", "PV02"))
    assert network.delay_ports(site, "master_to_rtu") == (
        (network.regions["R1"], "access1"),
        (network.regions["R1"], "access2"),
    )
    assert network.delay_ports(site, "rtu_to_master") == (
        (network.rtus[0], "p1"),
        (network.rtus[1], "p1"),
    )
    assert network.delay_ports(RegionScope(kind="region", region_id="R1"), "master_to_rtu") == (
        (network.master, "p0_1"),
    )
    assert network.delay_ports(GlobalScope(kind="global"), "rtu_to_master") == tuple(
        (ns, "uplink") for ns in network.regions.values()
    )
    with pytest.raises(ValueError, match="unknown delay"):
        network.delay_ports(site, "both")


def controller_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, multi: bool = False
) -> tuple[DelayFaultController, dict[str, Any], list[int]]:
    config = a02_scenario(
        StationScope(kind="station", station_ids=("PV01", "PV02") if multi else ("PV01",)),
        200,
        "master_to_rtu",
    )
    network = RegionalNetwork(config.background.simulation)
    network._owned.extend((network.master, *network.regions.values(), *network.rtus))
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for i, site in enumerate(network.config.protocol.stations, 1):
        link = site.access_link
        assert link is not None
        port = (network.regions[site.region_id], f"access{i}")
        network.shaped.append((*port, link, False))
        state[port] = {
            "namespace": port[0],
            "interface": port[1],
            "actual": {
                "kind": "netem",
                "handle": f"{i}:",
                "root": True,
                "drops": 0,
                "qlen": 0,
                "options": {
                    "limit": 10000,
                    "seed": link.seed,
                    "delay": {"delay": link.delay_ms / 1000, "jitter": link.jitter_ms / 1000},
                },
            },
        }

    def run(*args: str) -> str:
        ns = args[2]
        dev = args[args.index("dev") + 1]
        row = state[(ns, dev)]
        if "change" in args:
            delay = args[args.index("delay") + 1]
            row["actual"]["options"]["delay"]["delay"] = float(delay.removesuffix("ms")) / 1000
            return ""
        return json.dumps([row["actual"]])

    monkeypatch.setattr(network, "_run", run)
    # Only use the selected access fixtures for lifecycle tests.
    monkeypatch.setattr(network, "shaping_evidence", lambda: [])
    (tmp_path / "truth/fault").mkdir(parents=True)
    now = [10**9]
    (tmp_path / "scene.json").write_text(json.dumps({"start_mono_ns": now[0]}))
    monkeypatch.setattr("pvcomm.scenario.delay_fault.time.monotonic_ns", lambda: now[0])
    origin = ClockOrigin(10**12, now[0], 0)
    controller = DelayFaultController(tmp_path, config, network, origin)
    initial = copy.deepcopy(list(state.values()))
    manifest = {
        "run_id": config.background.simulation.protocol.run_id,
        "clock_origin": {"ts_utc_ns": origin.ts_utc_ns, "ts_mono_ns": origin.ts_mono_ns},
        "scene_clock": {"start_mono_ns": now[0]},
        "network": {
            "master": network.master,
            "rtus": network.rtus,
            "regions": network.regions,
            "netem_initial": initial,
            "netem_final": initial,
        },
    }
    return controller, manifest, now


def test_readback_restoration_and_independent_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, manifest, now = controller_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="not owned"):
        controller.network.change_delay("host", "eth0", 200)
    with pytest.raises(ValueError, match="configured owned"):
        controller.network.change_delay(controller.network.master, "eth0", 200)
    now[0] += 20 * 10**9
    controller.step()
    port = controller.ports[0]
    assert controller.network.delay_evidence(*port)["actual"]["options"]["delay"]["delay"] > 0.2
    now[0] += 30 * 10**9
    controller.step()
    controller.close()
    events = [
        json.loads(r) for r in (tmp_path / "truth/fault/events.jsonl").read_text().splitlines()
    ]
    assert audit_delay_operations(controller.config, manifest, events)["passed"]
    for field, value in (
        ("run_id", "other"),
        ("sim_elapsed_ms", -1),
        ("event_type", "apply_failed"),
    ):
        changed = copy.deepcopy(events)
        changed[1][field] = value
        assert not audit_delay_operations(controller.config, manifest, changed)["passed"]
    for field, value in (("drops", 1), ("handle", "999:")):
        changed = copy.deepcopy(events)
        changed[1]["after"]["actual"][field] = value
        assert not audit_delay_operations(controller.config, manifest, changed)["passed"]
    assert not audit_delay_operations(controller.config, manifest, events[:-1])["passed"]


def test_partial_apply_restores_and_cancel_is_censored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _, now = controller_fixture(tmp_path, monkeypatch, multi=True)
    original = controller.network.change_delay

    def fail(ns: str, dev: str, ms: int) -> dict[str, Any]:
        result = original(ns, dev, ms)
        if dev == "access2" and ms:
            raise RuntimeError("synthetic post-mutation failure")
        return result

    monkeypatch.setattr(controller.network, "change_delay", fail)
    now[0] += 20 * 10**9
    with pytest.raises(RuntimeError, match="post-mutation"):
        controller.step()
    controller.close()
    for port in controller.ports:
        assert controller.network.delay_evidence(*port) == controller.baselines[port]
    journal = (tmp_path / "truth/fault/events.jsonl").read_text()
    assert '"recovery_censored": true' in journal and '"event_type": "apply_failed"' in journal


def test_matching_rejects_ambiguous_cross_connection_and_missing() -> None:
    packet = {
        "src_ip": "synthetic",
        "dst_ip": "fixture",
        "src_port": 100,
        "dst_port": 2404,
        "connection_generation": 1,
        "data_hex": "unit_test_only",
        "packet_index": 1,
        "available_at": 100,
        "type_id": 13,
    }
    delivery = {**packet, "packet_index": 2, "available_at": 200_000_100}
    assert match_deliveries([packet], [delivery])["matched"][0]["delay_ms"] == 200
    assert not match_deliveries([packet, packet], [delivery])["matched"]
    assert match_deliveries([packet, packet], [delivery])["ambiguous"]
    assert (
        len(match_deliveries([packet], [{**delivery, "connection_generation": 2}])["unmatched"])
        == 2
    )
    assert not match_deliveries([packet], [])["matched"]


def test_bad_kernel_readback_and_restore_failure_remain_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _, now = controller_fixture(tmp_path, monkeypatch)
    original = controller.network._run

    def corrupt(*args: str) -> str:
        result = original(*args)
        if "change" not in args:
            rows = json.loads(result)
            rows[0]["options"]["limit"] = 1000
            return json.dumps(rows)
        return result

    monkeypatch.setattr(controller.network, "_run", corrupt)
    with pytest.raises(RuntimeError, match="baseline parameters"):
        controller.network.change_delay(*controller.ports[0], 200)
    monkeypatch.setattr(controller.network, "_run", original)
    now[0] += 20 * 10**9
    controller.step()
    monkeypatch.setattr(
        controller.network,
        "change_delay",
        lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic restore failure")),
    )
    with pytest.raises(RuntimeError, match="restore failure"):
        controller.close()
    assert '"event_type": "restore_failed"' in (tmp_path / "truth/fault/events.jsonl").read_text()


def test_measured_direction_and_missing_delivery_cannot_be_filled_from_plan() -> None:
    config = configuration()
    origin = 10**12
    events = [
        {"event_type": "fault_active", "ts_event_utc_ns": origin + 20 * 10**9},
        {"event_type": "restore_requested", "ts_event_utc_ns": origin + 50 * 10**9},
        {"event_type": "queue_drained", "ts_event_utc_ns": origin + 51 * 10**9},
    ]
    captures: dict[str, Any] = {"primary": {"apdus": []}}
    for i, station in enumerate(config.background.simulation.protocol.stations, 1):
        captures[f"diagnostic_rtu{i}"] = {"apdus": []}
        for reverse in (False, True):
            for phase, seconds in enumerate((5, 30, 80)):
                packet: dict[str, Any] = {
                    "src_ip": station.address if reverse else "master",
                    "dst_ip": "master" if reverse else station.address,
                    "src_port": 2404 if reverse else 12345,
                    "dst_port": 12345 if reverse else 2404,
                    "connection_generation": 1,
                    "data_hex": f"synthetic_only_{i}_{reverse}_{phase}",
                    "type_id": 13,
                    "available_at": origin + seconds * 10**9,
                    "packet_index": phase + 1,
                }
                delay = 210_000_000 if i == 1 and not reverse and phase == 1 else 10_000_000
                delivery = {**packet, "available_at": packet["available_at"] + delay}
                source, dest = (
                    (f"diagnostic_rtu{i}", "primary")
                    if reverse
                    else ("primary", f"diagnostic_rtu{i}")
                )
                captures[source]["apdus"].append(packet)
                captures[dest]["apdus"].append(delivery)
    manifest = {"scene_clock": {"start_utc_ns": origin}}
    assert measure_delays(config, manifest, events, captures)["passed"]
    damaged = copy.deepcopy(captures)
    # Removing the actual affected delivery must not produce a planned 200ms value.
    damaged["diagnostic_rtu1"]["apdus"].pop(1)
    report = measure_delays(config, manifest, events, damaged)
    assert not report["passed"]
    row = next(r for r in report["directions"] if r["targeted"])
    assert row["distributions"]["active"]["median_ms"] is None
    assert row["unmatched"]
    damaged = copy.deepcopy(captures)
    damaged["primary"]["apdus"][-2]["available_at"] += 500_000_000
    assert not measure_delays(config, manifest, events, damaged)["passed"]
