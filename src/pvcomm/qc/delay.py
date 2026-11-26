"""Independent A02 readback, actual P0/P1 delay and recovery verification."""

import hashlib
import json
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from pvcomm.config.faults import (
    FaultScenarioConfig,
    LinkDelay,
    instantiate_fault,
    load_fault_scenario,
)
from pvcomm.config.scenes import canonical_hash
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.qc.experiment import evaluate_experiment
from pvcomm.qc.pcap import read_apdus
from pvcomm.qc.schedule import audit_schedule, normal_transport_quality
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-a02-mechanism-v1"


def audit_delay_operations(
    config: FaultScenarioConfig, manifest: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    fault = config.fault
    if not isinstance(fault, LinkDelay):
        raise ValueError("requires A02")
    simulation = config.background.simulation
    network = manifest["network"]
    reverse = fault.direction == "rtu_to_master"
    # Resolve independently of the mutation implementation.
    expected: dict[tuple[str, str], Any] = {}
    for i, site in enumerate(simulation.protocol.stations, 1):
        if fault.scope.kind == "station" and site.station_id in config.target_stations():
            key = (
                (network["rtus"][i - 1], "p1")
                if reverse
                else (network["regions"][site.region_id], f"access{i}")
            )
            expected[key] = site.access_link
    if fault.scope.kind != "station":
        for i, region in enumerate(simulation.regions, 1):
            if fault.scope.kind == "global" or region.region_id == fault.scope.region_id:
                key = (
                    (network["regions"][region.region_id], "uplink")
                    if reverse
                    else (network["master"], f"p0_{i}")
                )
                expected[key] = region.uplink
    origin, scene = manifest["clock_origin"], manifest["scene_clock"]
    identity = bool(events) and len({r["event_id"] for r in events}) == len(events)
    identity &= all(
        r["run_id"] == manifest["run_id"]
        and r["fault_event_id"] == fault.event_id
        and r["ts_event_utc_ns"] == origin["ts_utc_ns"] + r["ts_mono_ns"] - origin["ts_mono_ns"]
        and r["sim_elapsed_ms"] == max(0, r["ts_mono_ns"] - scene["start_mono_ns"]) // 10**6
        for r in events
    )
    identity &= all(
        a["ts_mono_ns"] <= b["ts_mono_ns"] for a, b in zip(events, events[1:], strict=False)
    )
    active = [r for r in events if r["event_type"] == "fault_active"]
    drained = [r for r in events if r["event_type"] == "queue_drained"]
    finished = [r for r in events if r["event_type"] == "fault_finished"]
    phases = (
        len(active) == len(drained) == len(finished) == 1
        and set(active[0]["target_stations"]) == set(config.target_stations())
        and active[0]["ts_mono_ns"] < drained[0]["ts_mono_ns"] <= finished[0]["ts_mono_ns"]
        and finished[0]["reason"] == "scheduled_end"
        and finished[0]["was_applied"] is True
    )
    initial = {(r["namespace"], r["interface"]): r for r in network["netem_initial"]}
    final = {(r["namespace"], r["interface"]): r for r in network["netem_final"]}
    unchanged = (
        bool(initial)
        and initial.keys() == final.keys()
        and all(
            r["actual"]["options"] == final[k]["actual"]["options"]
            and r["actual"]["handle"] == final[k]["actual"]["handle"]
            and r["actual"]["drops"] == final[k]["actual"]["drops"] == 0
            for k, r in initial.items()
        )
    )
    checks = []
    for port, link in expected.items():
        own = [
            r
            for r in events
            if (r.get("before", {}).get("namespace"), r.get("before", {}).get("interface")) == port
        ]
        ok = [r["event_type"] for r in own] == [
            "apply_requested",
            "apply_verified",
            "restore_requested",
            "restore_verified",
        ]
        if ok and phases and port in initial:
            request, applied, restore, restored = own
            base = initial[port]["actual"]
            options = base["options"]
            delay = {
                **options,
                "delay": {
                    **options["delay"],
                    "delay": (link.delay_ms + fault.additional_delay_ms) / 1000,
                },
            }
            ok &= abs(options["delay"]["delay"] * 1000 - link.delay_ms) < 1e-6
            ok &= abs(options["delay"]["jitter"] * 1000 - link.jitter_ms) < 1e-6
            ok &= options["seed"] == link.seed + int(reverse) and options["limit"] == 10000
            ok &= request["direction"] == fault.direction
            ok &= request["before"]["actual"]["options"] == options
            ok &= applied["before"]["actual"]["options"] == options
            ok &= all(
                r["actual"]["options"] == delay
                for r in (applied["after"], restore["before"], restored["before"])
            )
            ok &= restored["after"]["actual"]["options"] == options
            ok &= request["sim_elapsed_ms"] >= fault.start_s * 1000
            ok &= applied["ts_mono_ns"] <= active[0]["ts_mono_ns"] <= restore["ts_mono_ns"]
            ok &= restore["ts_mono_ns"] - active[0]["ts_mono_ns"] >= fault.duration_s * 10**9
            ok &= restored["ts_mono_ns"] <= drained[0]["ts_mono_ns"]
            ok &= restore["reason"] == restored["reason"] == "scheduled_end"
            for event in own:
                for field in ("before", "after"):
                    if field in event:
                        readback = event[field]
                        ok &= (readback["namespace"], readback["interface"]) == port
                        ok &= readback["actual"]["handle"] == base["handle"]
                        ok &= readback["actual"]["drops"] == 0
        else:
            ok = False
        checks.append(
            {
                "namespace": port[0],
                "interface": port[1],
                "passed": bool(ok),
                "event_refs": [r["event_id"] for r in own],
            }
        )
    actual_ports = {
        (r["before"]["namespace"], r["before"]["interface"]) for r in events if "before" in r
    }
    drain_ok = False
    if len(drained) == 1:
        readbacks = drained[0]["readbacks"]
        drain_ok = (
            len(readbacks) == len(expected)
            and {(r["namespace"], r["interface"]) for r in readbacks} == set(expected)
            and all(
                r["actual"]["qlen"] == r["actual"]["drops"] == 0
                and r["actual"]["options"]
                == initial[(r["namespace"], r["interface"])]["actual"]["options"]
                for r in readbacks
            )
        )
    return {
        "passed": bool(
            identity
            and phases
            and unchanged
            and drain_ok
            and actual_ports == set(expected)
            and checks
            and all(r["passed"] for r in checks)
            and len(events) == len(expected) * 4 + 3
        ),
        "identity_and_time": bool(identity),
        "phases_valid": bool(phases),
        "baseline_and_restoration": bool(unchanged),
        "queue_drain": bool(drain_ok),
        "ports": checks,
    }


def match_deliveries(
    source: list[dict[str, Any]], destination: list[dict[str, Any]]
) -> dict[str, Any]:
    """Ambiguous repeated content is excluded, never paired by nearest time."""

    def group(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[
                tuple(
                    row[k]
                    for k in (
                        "src_ip",
                        "dst_ip",
                        "src_port",
                        "dst_port",
                        "connection_generation",
                        "data_hex",
                    )
                )
            ].append(row)
        return grouped

    sent, received = group(source), group(destination)
    matched, ambiguous, missing = [], [], []
    for key in sent.keys() | received.keys():
        left, right = sent.get(key, []), received.get(key, [])
        evidence = {
            "source_frame_refs": [r["packet_index"] for r in left],
            "destination_frame_refs": [r["packet_index"] for r in right],
        }
        if len(left) > 1 or len(right) > 1:
            ambiguous.append(evidence)
        elif not left or not right:
            missing.append(evidence)
        else:
            a, b = left[0], right[0]
            matched.append(
                {
                    **evidence,
                    "source_available_at": a["available_at"],
                    "destination_available_at": b["available_at"],
                    "delay_ms": (b["available_at"] - a["available_at"]) / 10**6,
                    "type_id": a["type_id"],
                }
            )
    return {
        "matched": sorted(matched, key=lambda r: r["source_available_at"]),
        "ambiguous": ambiguous,
        "unmatched": missing,
    }


def measure_delays(
    config: FaultScenarioConfig,
    manifest: dict[str, Any],
    events: list[dict[str, Any]],
    captures: dict[str, Any],
) -> dict[str, Any]:
    fault = config.fault
    assert isinstance(fault, LinkDelay)
    start = next(r["ts_event_utc_ns"] for r in events if r["event_type"] == "fault_active")
    end = min(r["ts_event_utc_ns"] for r in events if r["event_type"] == "restore_requested")
    drained = next(r["ts_event_utc_ns"] for r in events if r["event_type"] == "queue_drained")
    origin = manifest["scene_clock"]["start_utc_ns"]
    windows = {
        "baseline": [origin, start - 10**9],
        "active": [start + 5 * 10**9, end - 4 * 10**9],
        "recovered": [
            drained + 5 * 10**9,
            origin + config.background.simulation.protocol.duration_s * 10**9,
        ],
    }
    rows = []
    for i, station in enumerate(config.background.simulation.protocol.stations, 1):
        p0, p1 = captures["primary"]["apdus"], captures[f"diagnostic_rtu{i}"]["apdus"]
        for direction in ("master_to_rtu", "rtu_to_master"):
            reverse = direction == "rtu_to_master"
            source, destination = (p1, p0) if reverse else (p0, p1)

            measured = match_deliveries(
                [r for r in source if r["src_ip" if reverse else "dst_ip"] == station.address],
                [r for r in destination if r["src_ip" if reverse else "dst_ip"] == station.address],
            )
            distribution: dict[str, dict[str, Any]] = {}
            for phase, (begin, finish) in windows.items():
                values = [
                    r["delay_ms"]
                    for r in measured["matched"]
                    if begin <= r["source_available_at"] < finish
                ]
                distribution[phase] = {
                    "count": len(values),
                    "median_ms": statistics.median(values) if values else None,
                    "minimum_ms": min(values, default=None),
                    "maximum_ms": max(values, default=None),
                }
            targeted = (
                station.station_id in config.target_stations() and direction == fault.direction
            )
            base = distribution["baseline"]["median_ms"]
            active = distribution["active"]["median_ms"]
            recovery = distribution["recovered"]["median_ms"]
            ok = all(v is not None for v in (base, active, recovery)) and all(
                r["delay_ms"] >= 0 for r in measured["matched"]
            )
            if ok:
                # Diagnostic mechanism gate, independent of business deadlines
                # and the unchanged <=100ms same-endpoint alignment gate.
                ok &= (
                    (
                        0.8 * fault.additional_delay_ms
                        <= active - base
                        <= fault.additional_delay_ms + 150
                    )
                    if targeted
                    else abs(active - base) <= 100
                )
                ok &= abs(recovery - base) <= 100
            rows.append(
                {
                    "station_id": station.station_id,
                    "direction": direction,
                    "targeted": targeted,
                    "passed": bool(ok),
                    "distributions": distribution,
                    **measured,
                }
            )
    return {
        "passed": all(r["passed"] for r in rows),
        "windows_utc_ns": windows,
        "directions": rows,
        "meaning": "complete_APDU_causal_delivery_delay_including_TCP_head_of_line_wait",
        "retransmissions": "deduplicated_by_TCP_sequence_in_each_capture; not independent samples",
        "scope": "Oracle_diagnostic_facts_not_service_labels",
    }


def evaluate_delay(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    config = FaultScenarioConfig.model_validate_json(
        (root / "truth/fault/effective.json").read_bytes()
    )
    contract = manifest["fault_contract"]
    configuration_ok = (
        instantiate_fault(load_fault_scenario(root / "truth/fault/source.yaml"), manifest["run_id"])
        == config
        and canonical_hash(config.model_dump(mode="json")) == contract["effective_sha256"]
        and contract["scope"] == "Oracle"
        and contract["version"] == config.schema_version
        and all(
            hashlib.sha256((root / f"truth/fault/{name}").read_bytes()).hexdigest() == contract[key]
            for name, key in (("source.yaml", "source_sha256"), ("events.jsonl", "events_sha256"))
        )
        and json.loads((root / "truth/scenario/effective.json").read_bytes())
        == config.background.model_dump(mode="json")
        and json.loads((root / "power_config.json").read_bytes())
        == config.background.simulation.model_dump(mode="json")
    )
    events = read_jsonl([root / "truth/fault/events.jsonl"])
    operations = audit_delay_operations(config, manifest, events)
    experiment = evaluate_experiment(root)
    transport = normal_transport_quality(experiment)
    effects = (
        measure_delays(
            config,
            manifest,
            events,
            {Path(c["path"]).stem: read_apdus(root / c["path"]) for c in manifest["captures"]},
        )
        if operations["passed"]
        else None
    )
    # Only the post-restoration GI is required to satisfy normal schedule QC;
    # in-fault command outcomes remain unmodified in experiment assessments.
    raw = config.background.model_dump(mode="json")
    raw["business_schedule"] = [
        r
        for r in raw["business_schedule"]
        if r["kind"] == "interrogation"
        and r["at_s"] >= config.fault.start_s + config.fault.duration_s + 5
    ]
    recovery_ids = {r["schedule_id"] for r in raw["business_schedule"]}
    from pvcomm.config.scenes import ScenarioConfig

    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    terminal = read_jsonl(list((root / "truth").glob("terminal_events*.jsonl")))
    objects = read_jsonl([root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl"])
    recovery = audit_schedule(
        ScenarioConfig.model_validate(raw),
        [r for r in master if r.get("dispatch_id") is None or r.get("dispatch_id") in recovery_ids],
        terminal,
        [r for r in objects if r["type_id"] != 50],
        experiment["command_assessments"],
        [],
    )
    recovery["passed"] &= bool(recovery_ids)
    physical = read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
    continuity = {}
    begin = manifest["scene_clock"]["start_mono_ns"] + config.fault.start_s * 10**9
    end = begin + config.fault.duration_s * 10**9
    for site in config.background.simulation.protocol.stations:
        times = sorted(
            r["model_mono_ns"]
            for r in physical
            if r.get("source") == "pvlib_pvwatts_inverter_v02"
            and r["station_id"] == site.station_id
            and begin <= r["model_mono_ns"] < end
        )
        gaps = [(b - a) / 1e9 for a, b in zip(times, times[1:], strict=False)]
        continuity[site.station_id] = {
            "count": len(times),
            "maximum_gap_s": max(gaps, default=None),
            "passed": bool(gaps) and max(gaps) <= 1.5,
        }
    repository = Path(__file__).resolve().parents[3]
    evaluator = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    return {
        "qc_version": VERSION,
        "run_id": manifest["run_id"],
        "run_code_version": manifest["code_version"],
        "evaluation_code_version": evaluator,
        "evaluation_code_dirty": dirty,
        "experiment_quality": experiment,
        "no_unexplained_endpoint_loss": transport,
        "fault_verification": {
            "passed": bool(configuration_ok and operations["passed"]),
            "configuration_integrity": bool(configuration_ok),
            "operations": operations,
        },
        "communication_effects": effects,
        "recovery": recovery,
        "physical_continuity": continuity,
        "label_quality": {"status": "not_evaluated"},
        "passed": bool(
            configuration_ok
            and operations["passed"]
            and experiment["passed"]
            and transport["passed"]
            and effects
            and effects["passed"]
            and recovery["passed"]
            and all(r["passed"] for r in continuity.values())
            and not dirty
        ),
        "scope": "A02_mechanism_and_data_quality_not_formal_label_acceptance",
    }
