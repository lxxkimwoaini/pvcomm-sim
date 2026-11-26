"""Independent A01 mechanism evidence; deliberately not formal anomaly labels."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from pvcomm.config.faults import FaultScenarioConfig, instantiate_fault, load_fault_scenario
from pvcomm.config.scenes import canonical_hash
from pvcomm.qc.experiment import evaluate_experiment
from pvcomm.qc.pcap import read_apdus
from pvcomm.qc.socket_audit import account_socket_quality, audit_unobserved_tests
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-a01-mechanism-v4"


def audit_operations(
    config: FaultScenarioConfig, manifest: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    scope = config.fault.scope
    simulation = config.background.simulation
    regions = manifest["network"]["regions"]
    expected_ports = (
        {
            (regions[s.region_id], f"access{i}")
            for i, s in enumerate(simulation.protocol.stations, 1)
            if s.station_id in config.target_stations()
        }
        if scope.kind == "station"
        else {
            (regions[r], "uplink")
            for r in regions
            if scope.kind == "global" or r == scope.region_id
        }
    )
    expected_run = simulation.protocol.run_id
    scene = manifest["scene_clock"]
    origin = manifest["clock_origin"]
    identities = bool(events) and len({r["event_id"] for r in events}) == len(events)
    identities &= all(
        r["run_id"] == expected_run
        and r["fault_event_id"] == config.fault.event_id
        and r["ts_event_utc_ns"] == origin["ts_utc_ns"] + r["ts_mono_ns"] - origin["ts_mono_ns"]
        and r["sim_elapsed_ms"] == max(0, r["ts_mono_ns"] - scene["start_mono_ns"]) // 1_000_000
        for r in events
    )
    identities &= all(
        a["ts_mono_ns"] <= b["ts_mono_ns"] for a, b in zip(events, events[1:], strict=False)
    )
    port_checks = []
    for namespace, interface in sorted(expected_ports):
        own = [
            r
            for r in events
            if r.get("before", {}).get("namespace") == namespace
            and r.get("before", {}).get("interface") == interface
        ]
        kinds = [r["event_type"] for r in own]
        ok = kinds == ["apply_requested", "apply_verified", "restore_requested", "restore_verified"]
        if ok:
            request, apply, restore, restored = own
            ok &= all(
                r["before"]["bridge"] == "aggregate" and "UP" in r["before"]["flags"] for r in own
            )
            ok &= request["before"]["state"] == apply["before"]["state"] == "forwarding"
            ok &= (
                apply["after"]["state"]
                == restore["before"]["state"]
                == restored["before"]["state"]
                == "disabled"
            )
            ok &= restored["after"]["state"] == "forwarding"
            ok &= all(
                r["after"]["namespace"] == namespace
                and r["after"]["interface"] == interface
                and r["after"]["bridge"] == "aggregate"
                and "UP" in r["after"]["flags"]
                for r in (apply, restored)
            )
            ok &= request["sim_elapsed_ms"] >= config.fault.start_s * 1000
            ok &= (
                restore["ts_mono_ns"] - apply["ts_mono_ns"]
                >= config.fault.duration_s * 1_000_000_000
            )
            ok &= restore["reason"] == restored["reason"] == "scheduled_end"
        port_checks.append(
            {
                "namespace": namespace,
                "interface": interface,
                "passed": bool(ok),
                "event_refs": [r["event_id"] for r in own],
            }
        )
    known = {
        "apply_requested",
        "apply_verified",
        "fault_active",
        "restore_requested",
        "restore_verified",
        "fault_finished",
    }
    actual_ports = {
        (r["before"]["namespace"], r["before"]["interface"]) for r in events if "before" in r
    }
    active = [r for r in events if r["event_type"] == "fault_active"]
    finished = [r for r in events if r["event_type"] == "fault_finished"]
    phase_ok = len(active) == len(finished) == 1
    phase_ok &= bool(
        active
        and finished
        and active[0]["ts_mono_ns"] < finished[0]["ts_mono_ns"]
        and set(active[0]["target_stations"]) == set(config.target_stations())
        and finished[0]["reason"] == "scheduled_end"
        and finished[0]["was_applied"] is True
    )
    if phase_ok:
        phase_ok &= all(
            r["ts_mono_ns"] <= active[0]["ts_mono_ns"]
            if r["event_type"] == "apply_verified"
            else r["ts_mono_ns"] >= active[0]["ts_mono_ns"]
            if r["event_type"] == "restore_requested"
            else r["ts_mono_ns"] <= finished[0]["ts_mono_ns"]
            for r in events
            if r["event_type"] in ("apply_verified", "restore_requested", "restore_verified")
        )
    all_known = all(r["event_type"] in known for r in events)
    passed = (
        identities
        and all_known
        and phase_ok
        and actual_ports == expected_ports
        and bool(port_checks)
        and len(events) == 4 * len(expected_ports) + 2
        and all(p["passed"] for p in port_checks)
    )
    return {
        "passed": bool(passed),
        "identity_and_time": bool(identities),
        "phases_valid": bool(phase_ok),
        "known_events_only": all_known,
        "ports": port_checks,
        "actual_ports_match": actual_ports == expected_ports,
    }


def communication_effects(
    config: FaultScenarioConfig,
    events: list[dict[str, Any]],
    master: list[dict[str, Any]],
    states: list[dict[str, Any]],
    captures: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    active = next(r for r in events if r["event_type"] == "fault_active")
    restores = [r for r in events if r["event_type"] == "restore_requested"]
    restored = [r for r in events if r["event_type"] == "restore_verified"]
    # Exclude in-flight baseline packets; this is a diagnostic inner interval,
    # not a new freshness deadline or an online anomaly start.
    begin = active["ts_event_utc_ns"] + 1_000_000_000
    end = min(r["ts_event_utc_ns"] for r in restores)
    recovery_start = max(r["ts_event_utc_ns"] for r in restored)
    rows = {}
    for index, station in enumerate(config.background.simulation.protocol.stations, 1):
        source = [
            r
            for r in captures[f"diagnostic_rtu{index}"]["apdus"]
            if r["src_ip"] == station.address and r["type_id"] == 13
        ]
        received = [
            r
            for r in captures["primary"]["apdus"]
            if r["src_ip"] == station.address and r["type_id"] == 13
        ]
        sent_inside = [r for r in source if begin <= r["available_at"] < end]
        received_inside = [r for r in received if begin <= r["available_at"] < end]
        # TCP/IEC104 backpressure can stop new sends soon after a link fails.
        # Match actual P1 transmissions over the entire verified blocked phase
        # to P0 deliveries; do not require another send in the inner interval.
        arrivals: dict[tuple[Any, ...], deque[dict[str, Any]]] = defaultdict(deque)

        def signature(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(row[k] for k in ("src_ip", "dst_ip", "src_port", "dst_port", "data_hex"))

        for row in sorted(received, key=lambda r: r["available_at"]):
            arrivals[signature(row)].append(row)
        withheld = []
        for row in sorted(source, key=lambda r: r["available_at"]):
            queue = arrivals[signature(row)]
            arrival = queue.popleft() if queue else None
            if active["ts_event_utc_ns"] <= row["available_at"] < end and (
                arrival is None or arrival["available_at"] >= end
            ):
                withheld.append(
                    {
                        "p1_frame_ref": row["packet_index"],
                        "p1_available_at": row["available_at"],
                        "p0_frame_ref": arrival["packet_index"] if arrival else None,
                        "p0_available_at": arrival["available_at"] if arrival else None,
                    }
                )
        observations = [
            r
            for r in master
            if r.get("station_id") == station.station_id
            and r["event_type"] == "master_observed_point"
            and r.get("ioa") == 1001
            and r.get("quality_valid") is not False
        ]
        recovered = next(
            (
                r
                for r in sorted(observations, key=lambda r: r["available_at"])
                if r["available_at"] >= recovery_start
            ),
            None,
        )
        physical = sorted(
            (
                r
                for r in states
                if r.get("station_id") == station.station_id
                and r.get("source") == "pvlib_pvwatts_inverter_v02"
                and begin - 1_000_000_000 <= r["ts_event_utc_ns"] < end + 1_000_000_000
            ),
            key=lambda r: r["model_mono_ns"],
        )
        gaps = [
            (b["model_mono_ns"] - a["model_mono_ns"]) / 1e9
            for a, b in zip(physical, physical[1:], strict=False)
        ]
        rows[station.station_id] = {
            "targeted": station.station_id in config.target_stations(),
            "p1_measurement_apdus_inside": len(sent_inside),
            "p0_measurement_apdus_inside": len(received_inside),
            "p1_frame_refs": [r["packet_index"] for r in sent_inside],
            "p0_frame_refs": [r["packet_index"] for r in received_inside],
            "withheld_measurement_apdus": len(withheld),
            "withheld_evidence": withheld,
            "delivery_gap_observed": bool(withheld) and not received_inside,
            "master_measurements_inside": sum(
                begin <= r["available_at"] < end for r in observations
            ),
            "physical_samples_inside": len(physical),
            "maximum_physical_gap_s": max(gaps, default=None),
            "recovery_event": recovered["event_id"] if recovered else None,
            "recovery_utc_ns": recovered["available_at"] if recovered else None,
            "recovery_after_restoration_s": (recovered["available_at"] - recovery_start) / 1e9
            if recovered
            else None,
        }
    return {
        "verified_blocked_interval_utc_ns": [active["ts_event_utc_ns"], end],
        "diagnostic_interval_utc_ns": [begin, end],
        "restoration_completed_utc_ns": recovery_start,
        "stations": rows,
        "scope": "Oracle_diagnostic_facts_not_service_labels",
    }


def evaluate_fault(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    config = FaultScenarioConfig.model_validate_json(
        (root / "truth/fault/effective.json").read_bytes()
    )
    template = load_fault_scenario(root / "truth/fault/source.yaml")
    contract = manifest["fault_contract"]
    configuration_ok = (
        instantiate_fault(template, manifest["run_id"]) == config
        and manifest["run_id"] == config.background.simulation.protocol.run_id
        and contract["version"] == config.schema_version
        and contract["scope"] == "Oracle"
        and canonical_hash(config.model_dump(mode="json")) == contract["effective_sha256"]
        and hashlib.sha256((root / "truth/fault/source.yaml").read_bytes()).hexdigest()
        == contract["source_sha256"]
        and hashlib.sha256((root / "truth/fault/events.jsonl").read_bytes()).hexdigest()
        == contract["events_sha256"]
        and json.loads((root / "truth/scenario/effective.json").read_bytes())
        == config.background.model_dump(mode="json")
        and json.loads((root / "power_config.json").read_bytes())
        == config.background.simulation.model_dump(mode="json")
    )
    events = read_jsonl([root / "truth/fault/events.jsonl"])
    operations = audit_operations(config, manifest, events)
    experiment = evaluate_experiment(root)
    effects = None
    socket_evidence = None
    if operations["passed"]:
        socket_evidence = audit_unobserved_tests(root, events, config.target_stations())
        # Normal regression remains strict. Only independently audited, pre-write
        # TESTFR attempts during this A01 block can resolve extra send callbacks.
        # Every received callback and every actual captured APDU still corresponds.
        account_socket_quality(experiment, socket_evidence)
        effects = communication_effects(
            config,
            events,
            read_jsonl(list((root / "observed").glob("master_events*.jsonl"))),
            read_jsonl(list((root / "truth").glob("electrical_state*.jsonl"))),
            {Path(c["path"]).stem: read_apdus(root / c["path"]) for c in manifest["captures"]},
        )
    repository = Path(__file__).resolve().parents[3]
    evaluator = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    evaluator_dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    return {
        "qc_version": VERSION,
        "run_id": manifest["run_id"],
        "run_code_version": manifest["code_version"],
        "evaluation_code_version": evaluator,
        "evaluation_code_dirty": evaluator_dirty,
        "experiment_quality": experiment,
        "fault_verification": {
            "passed": configuration_ok and operations["passed"],
            "configuration_integrity": configuration_ok,
            "operations": operations,
        },
        "communication_effects": effects,
        "socket_evidence": socket_evidence,
        "label_quality": {"status": "not_evaluated"},
        "passed": experiment["passed"]
        and configuration_ok
        and operations["passed"]
        and not evaluator_dirty,
        "scope": "A01_mechanism_and_data_quality_not_formal_label_acceptance",
    }


def qc_fault(root: Path) -> dict[str, Any]:
    config = FaultScenarioConfig.model_validate_json(
        (root / "truth/fault/effective.json").read_bytes()
    )
    if config.fault.fault_type == "A02":
        from pvcomm.qc.delay import VERSION as DELAY_VERSION
        from pvcomm.qc.delay import evaluate_delay

        version, evaluate = DELAY_VERSION, evaluate_delay
    else:
        version, evaluate = VERSION, evaluate_fault
    destination = root / "qc" / version
    if destination.exists():
        raise FileExistsError(f"fault QC exists: {destination}")
    report = evaluate(root)
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fault-qc-", dir=destination.parent) as temp:
        stage = Path(temp) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return report
