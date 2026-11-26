"""Independent RTU mechanism evidence; injection is not a business label."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pvcomm.config.faults import (
    FaultScenarioConfig,
    TerminalFault,
    instantiate_fault,
    load_fault_scenario,
)
from pvcomm.config.scenes import ScenarioConfig, canonical_hash
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.qc.experiment import evaluate_experiment
from pvcomm.qc.maintenance import audit_maintenance
from pvcomm.qc.pcap import read_apdus
from pvcomm.qc.schedule import audit_schedule, normal_transport_quality
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-terminal-mechanism-v2"
GROUP_VERSION = "v04-terminal-mechanism-v3"


def version_for(config: FaultScenarioConfig) -> str:
    return GROUP_VERSION if config.background.maintenance_windows else VERSION


def measurement_ioas(row: dict[str, Any]) -> set[int]:
    """Read actual bounded monitoring ASDUs, including SQ and multi-object GI."""
    if row["type_id"] not in (1, 3, 13):
        return set()
    data = bytes.fromhex(row["data_hex"])
    size = 5 if row["type_id"] == 13 else 1
    count, sequence = data[7] & 127, bool(data[7] & 128)
    cursor, first, result = 12, 0, set()
    for i in range(count):
        if not sequence or i == 0:
            if len(data) < cursor + 3:
                raise ValueError("incomplete diagnostic IOA")
            first = int.from_bytes(data[cursor : cursor + 3], "little")
            cursor += 3
        if len(data) < cursor + size:
            raise ValueError("incomplete diagnostic measurement")
        result.add(first + i if sequence else first)
        cursor += size
    if cursor != len(data):
        raise ValueError("unexpected monitoring ASDU payload")
    return result


def audit_terminal_effects(
    config: FaultScenarioConfig,
    terminal: list[dict[str, Any]],
    master: list[dict[str, Any]],
    captures: dict[str, Any],
) -> dict[str, Any]:
    fault = config.fault
    if not isinstance(fault, TerminalFault):
        raise ValueError("requires terminal fault")
    results = {}
    p0 = captures["primary"]["apdus"]
    targets = config.target_stations()
    for index, station in enumerate(config.background.simulation.protocol.stations, 1):
        if station.station_id not in targets:
            continue
        own = [
            r
            for r in terminal
            if r.get("station_id") == station.station_id
            and r.get("fault_event_id") == fault.event_id
        ]
        applied = [r for r in own if r["event_type"] == "terminal_fault_applied"]
        restored = [r for r in own if r["event_type"] == "terminal_fault_restored"]
        holds = [r for r in own if r["event_type"] == "terminal_fault_feedback_hold"]
        ok = len(applied) == len(restored) == 1
        row: dict[str, Any] = {"passed": False}
        if not ok:
            results[station.station_id] = row
            continue
        active, end = applied[0], restored[0]
        if fault.fault_type == "A06":
            ok &= len(holds) == 1
            if holds:
                active = holds[0]
        expected_duration = (
            int(fault.missing_cycles or 0) * station.telemetry_period_s
            if fault.fault_type == "A04"
            else fault.duration_s
        )
        duration = (end["ts_mono_ns"] - active["ts_mono_ns"]) / 1e9
        ok &= end["reason"] == "scheduled_end" and duration >= expected_duration
        mask = set(active["suppressed_ioas"])
        ok &= not mask.intersection(active["registered_ioas"])
        ok &= not end["suppressed_ioas"] and {1001, 1006}.issubset(end["registered_ioas"])
        begin_utc, end_utc = active["ts_event_utc_ns"], end["ts_event_utc_ns"]
        wire = [r for r in p0 if r["src_ip"] == station.address]
        p1 = [
            r for r in captures[f"diagnostic_rtu{index}"]["apdus"] if r["src_ip"] == station.address
        ]
        # Keep boundary frames in the evidence, but exclude existing baseline in-flight
        # traffic from the suppression assertion. This is not a log-alignment tolerance.
        interior = (begin_utc + 500_000_000, end_utc)
        missing = {}
        for name, rows in (("p0", wire), ("p1", p1)):
            leaked = [
                r["packet_index"]
                for r in rows
                if interior[0] <= r["available_at"] < interior[1]
                and mask.intersection(measurement_ioas(r))
            ]
            missing[name] = {"unexpected_frame_refs": leaked}
            ok &= not leaked
        before = [r for r in wire if r["available_at"] < begin_utc and 1001 in measurement_ioas(r)]
        recovered = [
            r for r in wire if r["available_at"] >= end_utc and 1001 in measurement_ioas(r)
        ]
        ok &= bool(before and recovered)
        if fault.fault_type in ("A04", "A08"):
            expected_mask = set(end["registered_ioas"]) - {3001}
            if fault.fault_type == "A04":
                expected_mask -= {1006}
            ok &= mask == expected_mask and {1001, 1002, 1003}.issubset(mask)
        if fault.fault_type == "A06":
            ok &= mask == {1001, 1006}
            other = [
                r
                for r in wire
                if interior[0] <= r["available_at"] < interior[1]
                and measurement_ioas(r).intersection({1002, 1003, 1008})
            ]
            accepted = [
                r
                for r in terminal
                if r.get("station_id") == station.station_id
                and r["event_type"] == "terminal_command_accepted"
                and active["ts_mono_ns"] <= r["ts_mono_ns"] < end["ts_mono_ns"]
            ]
            acks = [
                r
                for r in wire
                if begin_utc <= r["available_at"] < end_utc
                and r["type_id"] == 50
                and r["cot"] == 7
                and not bytes.fromhex(r["data_hex"])[8] & 0xC0
            ]
            ok &= bool(other and accepted and acks)
            row.update(
                unaffected_measurement_frames=[r["packet_index"] for r in other],
                accepted_events=[r["event_id"] for r in accepted],
                confirmation_frame_refs=[r["packet_index"] for r in acks],
            )
        if fault.fault_type == "A05":
            starts = [r for r in own if r["event_type"] == "terminal_response_delay_started"]
            stops = [r for r in own if r["event_type"] == "terminal_response_delay_finished"]
            measured = (
                (stops[0]["ts_mono_ns"] - starts[0]["begin_mono_ns"]) / 1e9
                if len(starts) == len(stops) == 1
                else None
            )
            delayed_acks = [
                r
                for r in wire
                if starts
                and r["available_at"] >= starts[0]["ts_event_utc_ns"]
                and r["type_id"] == 50
                and r["cot"] == 7
                and not bytes.fromhex(r["data_hex"])[8] & 0xC0
            ]
            ok &= bool(
                measured is not None
                and measured >= int(fault.response_delay_s or 0)
                and delayed_acks
                and not stops[0]["cancelled"]
            )
            row.update(
                actual_processing_wait_s=measured,
                confirmation_frame_refs=[r["packet_index"] for r in delayed_acks],
            )
        if fault.fault_type == "A08":
            resumed = [
                r
                for r in terminal
                if r.get("station_id") == station.station_id
                and r["event_type"] == "terminal_resumed"
                and begin_utc <= r["ts_event_utc_ns"] < end_utc
            ]
            gi = [
                r
                for r in wire
                if interior[0] <= r["available_at"] < end_utc
                and r["type_id"] == 100
                and r["cot"] in (7, 10)
            ]
            opened = [
                r
                for r in master
                if r.get("station_id") == station.station_id
                and r["event_type"] == "master_connection_state"
                and r["state"] == "OPEN"
                and begin_utc <= r["available_at"] < end_utc
            ]
            ok &= bool(resumed and opened and {7, 10}.issubset({r["cot"] for r in gi}))
            row.update(
                resumed_event_refs=[r["event_id"] for r in resumed],
                live_session_gi_frame_refs=[r["packet_index"] for r in gi],
                open_event_refs=[r["event_id"] for r in opened],
            )
        row.update(
            passed=bool(ok),
            duration_s=duration,
            begin_utc_ns=begin_utc,
            end_utc_ns=end_utc,
            applied_event=applied[0]["event_id"],
            hold_event=active["event_id"],
            restored_event=end["event_id"],
            suppressed_ioas=sorted(mask),
            suppression=missing,
            recovery_frame_ref=recovered[0]["packet_index"] if recovered else None,
            recovery_utc_ns=recovered[0]["available_at"] if recovered else None,
        )
        results[station.station_id] = row
    return {
        "passed": bool(results) and all(r["passed"] for r in results.values()),
        "stations": results,
    }


def evaluate_terminal(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    config = FaultScenarioConfig.model_validate_json(
        (root / "truth/fault/effective.json").read_bytes()
    )
    contract = manifest["fault_contract"]
    configuration = (
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
    experiment = evaluate_experiment(root)
    transport = normal_transport_quality(experiment)
    terminal = read_jsonl(list((root / "truth").glob("terminal_events*.jsonl")))
    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    captures = {Path(c["path"]).stem: read_apdus(root / c["path"]) for c in manifest["captures"]}
    effects = audit_terminal_effects(config, terminal, master, captures)
    events = read_jsonl([root / "truth/fault/events.jsonl"])
    journal = (
        len([r for r in events if r["event_type"] == "apply_verified"])
        == len(config.target_stations())
        and len([r for r in events if r["event_type"] == "restore_verified"])
        == len(config.target_stations())
        and all(
            r["run_id"] == manifest["run_id"] and r["fault_event_id"] == config.fault.event_id
            for r in events
        )
    )
    raw = config.background.model_dump(mode="json")
    raw["business_schedule"] = [
        r for r in raw["business_schedule"] if r["schedule_id"] == "recovery"
    ]
    objects = read_jsonl([root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl"])
    recovery = audit_schedule(
        ScenarioConfig.model_validate(raw),
        [r for r in master if r.get("dispatch_id") in (None, "recovery")],
        terminal,
        [r for r in objects if r["type_id"] != 50],
        experiment["command_assessments"],
        [],
    )
    recovery["passed"] &= bool(raw["business_schedule"])
    states = read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
    maintenance = (
        audit_maintenance(config.background, manifest, master, terminal, objects, states)
        if config.background.maintenance_windows
        else {"passed": True, "stations": {}}
    )
    continuity = {}
    for station, effect in effects["stations"].items():
        times = sorted(
            r["model_mono_ns"]
            for r in states
            if r["station_id"] == station
            and r.get("source") == "pvlib_pvwatts_inverter_v02"
            and effect.get("begin_utc_ns", 0) <= r["ts_event_utc_ns"] < effect.get("end_utc_ns", 0)
        )
        gaps = [(b - a) / 1e9 for a, b in zip(times, times[1:], strict=False)]
        mapping = manifest["clock_origin"]
        begin_mono = effect.get("begin_utc_ns", 0) - mapping["ts_utc_ns"] + mapping["ts_mono_ns"]
        end_mono = effect.get("end_utc_ns", 0) - mapping["ts_utc_ns"] + mapping["ts_mono_ns"]
        coverage = bool(times and times[0] - begin_mono <= 1.5e9 and end_mono - times[-1] <= 1.5e9)
        continuity[station] = {
            "passed": bool(gaps) and max(gaps) <= 1.5 and coverage,
            "boundary_coverage": coverage,
            "maximum_gap_s": max(gaps, default=None),
            "samples": len(times),
        }
    repository = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    return dict(
        qc_version=version_for(config),
        run_id=manifest["run_id"],
        run_code_version=manifest["code_version"],
        evaluation_code_version=sha,
        evaluation_code_dirty=dirty,
        experiment_quality=experiment,
        no_unexplained_endpoint_loss=transport,
        communication_effects=effects,
        fault_verification={
            "passed": bool(configuration and journal and effects["passed"]),
            "configuration_integrity": bool(configuration),
            "journal": bool(journal),
        },
        recovery=recovery,
        physical_continuity=continuity,
        authorized_maintenance=maintenance,
        label_quality={"status": "not_evaluated"},
        passed=bool(
            configuration
            and journal
            and experiment["passed"]
            and transport["passed"]
            and effects["passed"]
            and recovery["passed"]
            and maintenance["passed"]
            and all(r["passed"] for r in continuity.values())
            and not dirty
        ),
        scope="terminal_mechanism_and_quality_not_formal_labels",
    )
