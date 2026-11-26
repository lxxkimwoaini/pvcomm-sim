"""Explicit synthetic kernel/time fixtures; not real fault acceptance evidence."""

import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.cli import main
from pvcomm.clock import ClockOrigin
from pvcomm.config.fault_scenes import a01_scenario
from pvcomm.config.faults import (
    FaultScenarioConfig,
    GlobalScope,
    RegionScope,
    StationScope,
    instantiate_fault,
    load_fault_scenario,
)
from pvcomm.network.regional import RegionalNetwork
from pvcomm.qc.fault import audit_operations, communication_effects
from pvcomm.scenario.link_fault import LinkFaultController


def fixture_config() -> FaultScenarioConfig:
    return a01_scenario(StationScope(kind="station", station_ids=("PV01",)), 5)


@pytest.mark.parametrize(
    "scope,targets,port_count",
    [
        (StationScope(kind="station", station_ids=("PV01", "PV03")), ("PV01", "PV03"), 2),
        (RegionScope(kind="region", region_id="R1"), ("PV01", "PV02"), 1),
        (GlobalScope(kind="global"), ("PV01", "PV02", "PV03"), 2),
    ],
)
def test_scope_resolves_configuration(
    scope: Any, targets: tuple[str, ...], port_count: int
) -> None:
    config = a01_scenario(scope, 20)
    assert config.target_stations() == targets
    network = RegionalNetwork(config.background.simulation)
    assert len(network.interruption_ports(scope)) == port_count


@pytest.mark.parametrize(
    "change",
    [
        {"duration_s": 6},
        {"duration_s": 5.0},
        {"duration_s": "5"},
        {"fault_type": "A02"},
        {"start_s": 1},
        {"start_s": 140},
        {"scope": {"kind": "station", "station_ids": ["PV99"]}},
        {"scope": {"kind": "station", "station_ids": ["PV01", "PV01"]}},
        {"scope": {"kind": "region", "region_id": "R9"}},
        {"scope": {"kind": "global", "station_ids": ["PV01"]}},
    ],
)
def test_invalid_fault_contract_fails_closed(change: dict[str, Any]) -> None:
    raw = fixture_config().model_dump(mode="json")
    raw["fault"].update(change)
    with pytest.raises(ValueError):
        FaultScenarioConfig.model_validate(raw)


def test_fault_source_cli_identity_and_no_combination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    template = fixture_config()
    source = tmp_path / "fault.yaml"
    source.write_text(template.model_dump_json(), encoding="utf-8")
    assert load_fault_scenario(source) == template
    assert (
        instantiate_fault(template, "run_fixture").background.simulation.protocol.run_id
        == "run_fixture"
    )
    assert main(["validate-fault-scenario", "--config", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["target_stations"] == ["PV01"]
    assert main(["validate-scenario", "--config", str(source)]) == 2
    raw = template.model_dump(mode="json")
    raw["background"]["simulation"]["protocol"]["interruption"] = dict(
        station_id="PV01", start_s=40, duration_s=20, planned=True
    )
    with pytest.raises(ValueError):
        FaultScenarioConfig.model_validate(raw)
    source.write_text("schema_version: 0.4-fault.1\nschema_version: other\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_fault_scenario(source)


def kernel_fixture(
    config: FaultScenarioConfig, monkeypatch: pytest.MonkeyPatch
) -> tuple[RegionalNetwork, dict[tuple[str, str], str]]:
    network = RegionalNetwork(config.background.simulation)
    network._owned.extend(network.regions.values())
    states: dict[tuple[str, str], str] = {}

    def run(*args: str) -> str:
        if args[:2] == ("netns", "exec"):
            states[(args[2], args[7])] = "forwarding" if args[-1] == "3" else "disabled"
            return ""
        namespace, interface = args[1], args[-1]
        return json.dumps(
            [
                {
                    "master": "aggregate",
                    "flags": ["UP", "LOWER_UP"],
                    "linkinfo": {
                        "info_slave_data": {
                            "state": states.get((namespace, interface), "forwarding")
                        }
                    },
                }
            ]
        )

    monkeypatch.setattr(network, "_run", run)
    return network, states


def controller_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, multi: bool = False
) -> tuple[LinkFaultController, dict[tuple[str, str], str], list[int], dict[str, Any]]:
    config = a01_scenario(
        StationScope(kind="station", station_ids=("PV01", "PV02") if multi else ("PV01",)), 5
    )
    (tmp_path / "truth/fault").mkdir(parents=True)
    (tmp_path / "scene.json").write_text(
        json.dumps({"start_mono_ns": 1_000_000_000}), encoding="utf-8"
    )
    now = [1_000_000_000]
    monkeypatch.setattr("pvcomm.scenario.link_fault.time.monotonic_ns", lambda: now[0])
    network, states = kernel_fixture(config, monkeypatch)
    origin = ClockOrigin(1_000_000_000_000, 1_000_000_000, 0)
    controller = LinkFaultController(tmp_path, config, network, origin)
    manifest = {
        "network": {"regions": network.regions},
        "scene_clock": {"start_mono_ns": now[0]},
        "clock_origin": {"ts_utc_ns": origin.ts_utc_ns, "ts_mono_ns": origin.ts_mono_ns},
    }
    return controller, states, now, manifest


def test_real_boundary_and_independent_operation_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, states, now, manifest = controller_fixture(tmp_path, monkeypatch)
    now[0] += 19_999_999_999
    controller.step()
    assert not states
    now[0] += 1
    controller.step()
    assert set(states.values()) == {"disabled"}
    now[0] += 4_999_999_999
    controller.step()
    assert set(states.values()) == {"disabled"}
    now[0] += 1
    controller.step()
    controller.close()
    assert set(states.values()) == {"forwarding"}
    events = [
        json.loads(line)
        for line in (tmp_path / "truth/fault/events.jsonl").read_text().splitlines()
    ]
    assert audit_operations(controller.config, manifest, events)["passed"]
    for field, value in (
        ("run_id", "other_run"),
        ("fault_event_id", "other_event"),
        ("sim_elapsed_ms", -1),
    ):
        damaged = json.loads(json.dumps(events))
        damaged[1][field] = value
        assert not audit_operations(controller.config, manifest, damaged)["passed"]
    damaged = json.loads(json.dumps(events))
    damaged[1]["after"]["state"] = "forwarding"
    assert not audit_operations(controller.config, manifest, damaged)["passed"]
    assert not audit_operations(controller.config, manifest, events[:-1])["passed"]


def test_partial_application_and_cancel_restore_every_touched_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, states, now, manifest = controller_fixture(tmp_path, monkeypatch, multi=True)
    original = controller.network.set_bridge_forwarding

    def failing(namespace: str, interface: str, *, enabled: bool) -> dict[str, Any]:
        result = original(namespace, interface, enabled=enabled)
        if interface == "access2" and not enabled:
            raise RuntimeError("synthetic post-mutation failure")
        return result

    monkeypatch.setattr(controller.network, "set_bridge_forwarding", failing)
    now[0] += 20_000_000_000
    with pytest.raises(RuntimeError, match="post-mutation"):
        controller.step()
    assert set(states.values()) == {"disabled"}
    controller.close()
    assert len(states) == 2 and set(states.values()) == {"forwarding"}
    events = [
        json.loads(line)
        for line in (tmp_path / "truth/fault/events.jsonl").read_text().splitlines()
    ]
    assert not audit_operations(controller.config, manifest, events)["passed"]


def test_restoration_error_is_preserved_and_foreign_port_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, states, now, _ = controller_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="not owned"):
        controller.network.set_bridge_forwarding("host_namespace", "eth0", enabled=False)
    with pytest.raises(ValueError, match="not a configured"):
        controller.network.set_bridge_forwarding(
            next(iter(controller.network.regions.values())), "eth0", enabled=False
        )
    assert not states
    now[0] += 20_000_000_000
    controller.step()
    monkeypatch.setattr(
        controller.network,
        "set_bridge_forwarding",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic restore failure")),
    )
    with pytest.raises(RuntimeError, match="restore failure"):
        controller.close()
    assert '"event_type": "restore_failed"' in (tmp_path / "truth/fault/events.jsonl").read_text()


def test_backpressure_uses_matched_p1_p0_delivery_not_later_sends() -> None:
    config = fixture_config()
    start = 1_000_000_000_000
    events = [
        {"event_type": "fault_active", "ts_event_utc_ns": start},
        {"event_type": "restore_requested", "ts_event_utc_ns": start + 5_000_000_000},
        {"event_type": "restore_verified", "ts_event_utc_ns": start + 5_010_000_000},
    ]
    packet = {
        "src_ip": "10.204.1.2",
        "dst_ip": "10.204.1.1",
        "src_port": 2404,
        "dst_port": 12345,
        "type_id": 13,
        "data_hex": "synthetic_fixture_not_a_wire_frame",
        "packet_index": 10,
        "available_at": start + 100_000_000,
    }
    delivery = {**packet, "packet_index": 20, "available_at": start + 6_000_000_000}
    captures: dict[str, dict[str, Any]] = {
        "primary": {"apdus": [delivery]},
        "diagnostic_rtu1": {"apdus": [packet]},
        "diagnostic_rtu2": {"apdus": []},
        "diagnostic_rtu3": {"apdus": []},
    }
    row = communication_effects(config, events, [], [], captures)["stations"]["PV01"]
    assert row["p1_measurement_apdus_inside"] == 0
    assert row["delivery_gap_observed"] and row["withheld_measurement_apdus"] == 1
    assert row["withheld_evidence"][0]["p0_frame_ref"] == 20
    # An actually delivered early packet is not withheld just because the
    # diagnostic inner window contains no traffic.
    delivery["available_at"] = start + 200_000_000
    assert not communication_effects(config, events, [], [], captures)["stations"]["PV01"][
        "delivery_gap_observed"
    ]
    delivery["available_at"] = start + 5_000_000_000
    assert (
        communication_effects(config, events, [], [], captures)["stations"]["PV01"][
            "withheld_measurement_apdus"
        ]
        == 1
    )
    # Reused APDU content on another TCP connection cannot prove delivery.
    delivery["available_at"] = start + 200_000_000
    delivery["dst_port"] = 54321
    assert (
        communication_effects(config, events, [], [], captures)["stations"]["PV01"][
            "withheld_measurement_apdus"
        ]
        == 1
    )
    delivery["dst_port"] = packet["dst_port"]
    # A P0 delivery may be consumed only once when identical content repeats.
    captures["diagnostic_rtu1"]["apdus"].append({**packet, "packet_index": 11})
    assert (
        communication_effects(config, events, [], [], captures)["stations"]["PV01"][
            "withheld_measurement_apdus"
        ]
        == 1
    )
    # Silence without an actual P1 transmission is not evidence of withholding.
    captures["diagnostic_rtu1"]["apdus"].clear()
    assert not communication_effects(config, events, [], [], captures)["stations"]["PV01"][
        "delivery_gap_observed"
    ]
