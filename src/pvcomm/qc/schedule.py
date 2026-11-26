"""Diagnostic normal schedule acceptance; not an anomaly label generator."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.business.dispatch import allocate
from pvcomm.config.scenes import (
    AllocationTask,
    InterrogationTask,
    ScenarioConfig,
    SetpointTask,
    canonical_hash,
    instantiate_scenario,
    load_scenario,
)
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.qc.experiment import evaluate_experiment
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-normal-schedule-v2"


def normal_transport_quality(experiment: dict[str, Any]) -> dict[str, Any]:
    """Normal cases retain the original strict P0/P1 correspondence gate."""
    endpoints = experiment["transport_observations"]
    return {
        "passed": bool(endpoints) and all(r["same_bytes"] for r in endpoints.values()),
        "evidence": endpoints,
    }


def audit_schedule(
    scenario: ScenarioConfig,
    master: list[dict[str, Any]],
    terminal: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    assessments: dict[str, list[dict[str, Any]]],
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare intent to independent protocol/terminal evidence, fail on omissions."""
    tasks = scenario.expanded_schedule()
    started = [r for r in master if r["event_type"] == "master_schedule_started"]
    finished = [r for r in master if r["event_type"] == "master_schedule_finished"]
    commands = [r for r in master if r["event_type"] == "master_command_sent"]
    not_sent = [r for r in master if r["event_type"] == "master_command_not_sent"]
    expected_commands = {
        (task.schedule_id, station)
        for task in tasks
        if not isinstance(task, InterrogationTask)
        for station in task.station_ids
    }
    actual_commands = [(r.get("dispatch_id"), r["station_id"]) for r in commands]
    wire_commands = [r for r in objects if r["type_id"] == 50 and r["cot"] == 6]
    plan_valid = (
        len(actual_commands) == len(expected_commands)
        and set(actual_commands) == expected_commands
        and not not_sent
        and len(started) == len(finished) == len(tasks)
        and len(wire_commands) == len(commands)
    )
    task_evidence = []
    all_assessed = {r["command_event"]: r for rows in assessments.values() for r in rows}
    stations = {s.station_id: s for s in scenario.simulation.protocol.stations}
    for task in tasks:
        begin = [r for r in started if r["dispatch_id"] == task.schedule_id]
        end = [r for r in finished if r["dispatch_id"] == task.schedule_id]
        ok = len(begin) == len(end) == 1
        ok &= bool(
            begin
            and end
            and begin[0]["sim_elapsed_ms"] >= task.at_s * 1000
            and begin[0]["ts_mono_ns"] <= end[0]["ts_mono_ns"]
            and begin[0]["station_ids"] == list(task.station_ids)
            and begin[0]["kind"] == task.kind
        )
        evidence: dict[str, Any] = {
            "schedule_id": task.schedule_id,
            "started_event": begin[0]["event_id"] if len(begin) == 1 else None,
            "finished_event": end[0]["event_id"] if len(end) == 1 else None,
            "start_lateness_ms": begin[0]["sim_elapsed_ms"] - task.at_s * 1000
            if len(begin) == 1
            else None,
        }
        if isinstance(task, InterrogationTask):
            gi_evidence = []
            for station_id in task.station_ids:
                requests = [
                    r
                    for r in master
                    if r["event_type"] == "master_interrogation_requested"
                    and r["station_id"] == station_id
                    and r["dispatch_id"] == task.schedule_id
                ]
                results = [
                    r
                    for r in master
                    if r["event_type"] == "master_interrogation_result"
                    and r["station_id"] == station_id
                    and r["dispatch_id"] == task.schedule_id
                ]
                address = stations[station_id].address
                own = [
                    r
                    for r in objects
                    if r["ca"] == stations[station_id].common_address
                    and requests
                    and results
                    and requests[0]["available_at"]
                    <= r["available_at"]
                    < results[0]["available_at"]
                ]
                wire = [
                    r
                    for r in own
                    if r["type_id"] == 100 and r["cot"] == 6 and r["dst_ip"] == address
                ]
                responses = [
                    r
                    for r in own
                    if wire
                    and r["flow_id"] == wire[0]["flow_id"]
                    and r["src_ip"] == address
                    and not r.get("negative_confirm")
                    and not r.get("test_flag")
                    and r["available_at"] >= wire[0]["available_at"]
                ]
                cots = {r["cot"] for r in responses if r["type_id"] == 100}
                measurements = {r["ioa"] for r in responses if r["cot"] == 20}
                gi_ok = (
                    len(requests) == len(results) == len(wire) == 1
                    and results[0]["success"]
                    and {7, 10}.issubset(cots)
                    and {1001, 1006}.issubset(measurements)
                )
                ok &= gi_ok
                gi_evidence.append(
                    {
                        "station_id": station_id,
                        "passed": bool(gi_ok),
                        "request_event": requests[0]["event_id"] if len(requests) == 1 else None,
                        "response_apdus": sorted({r["apdu_id"] for r in responses}),
                    }
                )
            evidence["interrogations"] = gi_evidence
        else:
            selected = [r for r in commands if r.get("dispatch_id") == task.schedule_id]
            command_evidence = []
            for command in selected:
                assessment = all_assessed.get(command["event_id"])
                command_ok = bool(
                    assessment
                    and assessment["transaction_evidence_valid"]
                    and assessment["outcome"] in ("accepted_complete", "rejected")
                    and begin
                    and end
                    and begin[0]["ts_mono_ns"] <= command["ts_mono_ns"] <= end[0]["ts_mono_ns"]
                )
                if isinstance(task, SetpointTask):
                    command_ok &= (
                        command["station_id"] in task.targets_kw
                        and abs(command["target_kw"] - task.targets_kw[command["station_id"]])
                        <= 0.0001
                    )
                if assessment and assessment["outcome"] == "rejected":
                    rejection = [
                        r for r in terminal if r["event_id"] == assessment["rejection_event"]
                    ]
                    preceding = sorted(
                        (
                            r
                            for r in states
                            if r["station_id"] == command["station_id"]
                            and r.get("model_mono_ns", 0) <= command["ts_mono_ns"]
                        ),
                        key=lambda r: r.get("model_mono_ns", 0),
                    )
                    previous = preceding[-1] if preceding else None
                    command_ok &= bool(
                        previous
                        and rejection
                        and (not previous["enabled"] or not previous["connected"])
                        and not previous["faulted"]
                        and rejection[0]["accepted_setpoint_kw"] == previous["p_set_accepted_kw"]
                    )
                ok &= command_ok
                command_evidence.append({"passed": command_ok, "assessment": assessment})
            if isinstance(task, AllocationTask):
                allocations = [
                    r
                    for r in master
                    if r["event_type"] == "master_dispatch_allocated"
                    and r["dispatch_id"] == task.schedule_id
                ]
                ok &= bool(
                    len(allocations) == 1
                    and not allocations[0]["excluded_observations"]
                    and set(allocations[0]["targets_kw"]) == set(task.station_ids)
                    and abs(
                        allocations[0]["allocated_kw"]
                        + allocations[0]["unallocated_kw"]
                        - task.total_kw
                    )
                    < 0.0001
                )
                if len(allocations) == 1:
                    allocation = allocations[0]
                    inputs = allocation.get("observed_available_kw")
                    if task.allocation == "observed_available":
                        ok &= isinstance(inputs, dict) and set(inputs) == set(task.station_ids)
                        if isinstance(inputs, dict):
                            for station_id, value in inputs.items():
                                observations = [
                                    r
                                    for r in master
                                    if r["event_type"] == "master_observed_point"
                                    and r.get("station_id") == station_id
                                    and r.get("ioa") == 1008
                                    and r.get("quality_valid") is not False
                                    and r.get("value") == value
                                    and 0
                                    <= allocation["available_at"] - r["available_at"]
                                    < (stations[station_id].telemetry_period_s * 2 + 1)
                                    * 1_000_000_000
                                ]
                                ok &= bool(observations)
                    else:
                        ok &= inputs is None
                    if inputs is None or (
                        isinstance(inputs, dict) and set(inputs) == set(task.station_ids)
                    ):
                        expected_targets, remainder = allocate(
                            task.total_kw,
                            {s: stations[s].capacity_kw for s in task.station_ids},
                            inputs,
                        )
                        ok &= allocation["targets_kw"] == expected_targets
                        ok &= abs(allocation["unallocated_kw"] - remainder) < 0.0001
                        ok &= all(
                            abs(r["target_kw"] - expected_targets.get(r["station_id"], -1)) < 0.0001
                            for r in selected
                        )
                evidence["allocation_events"] = [r["event_id"] for r in allocations]
            evidence["commands"] = command_evidence
        evidence["passed"] = bool(ok)
        task_evidence.append(evidence)
    return {
        "passed": plan_valid and all(t["passed"] for t in task_evidence),
        "plan_membership_valid": plan_valid,
        "expected_commands": len(expected_commands),
        "actual_commands": len(commands),
        "wire_commands": len(wire_commands),
        "commands_not_sent": [r["event_id"] for r in not_sent],
        "tasks": task_evidence,
    }


def qc_schedule(root: Path) -> dict[str, Any]:
    destination = root / "qc" / VERSION
    if destination.exists():
        raise FileExistsError(f"schedule QC exists: {destination}")
    folder = root / "truth/scenario"
    scenario = ScenarioConfig.model_validate_json((folder / "effective.json").read_bytes())
    manifest = json.loads((root / "manifest.json").read_bytes())
    metadata = manifest["scenario_contract"]
    plan = json.loads((folder / "expanded-plan.json").read_bytes())
    integrity = (
        instantiate_scenario(load_scenario(folder / "source.yaml"), manifest["run_id"]) == scenario
        and canonical_hash(scenario.model_dump(mode="json")) == metadata["effective_sha256"]
        and hashlib.sha256((folder / "source.yaml").read_bytes()).hexdigest()
        == metadata["source_sha256"]
        and plan == [t.model_dump(mode="json") for t in scenario.expanded_schedule()]
        and canonical_hash(plan) == metadata["plan_sha256"]
        and scenario.simulation.model_dump(mode="json")
        == json.loads((root / "power_config.json").read_bytes())
    )
    experiment = evaluate_experiment(root)
    transport = normal_transport_quality(experiment)
    repository = Path(__file__).resolve().parents[3]
    evaluator = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    evaluator_dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    terminal = read_jsonl(list((root / "truth").glob("terminal_events*.jsonl")))
    objects = read_jsonl([root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl"])
    states = [
        r
        for r in read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
        if r.get("source") == "pvlib_pvwatts_inverter_v02"
    ]
    business = audit_schedule(
        scenario, master, terminal, objects, experiment["command_assessments"], states
    )
    telemetry = {}
    for station in scenario.simulation.protocol.stations:
        seen = {
            r["ioa"]
            for r in master
            if r["event_type"] == "master_observed_point"
            and r.get("station_id") == station.station_id
            and r.get("quality_valid") is not False
            and r["value"] is not None
            and r["sim_elapsed_ms"] >= 0
        }
        gi_cots = {
            r["cot"] for r in objects if r["type_id"] == 100 and r["ca"] == station.common_address
        }
        telemetry[station.station_id] = {
            "passed": {1001, 1002, 1003, 1006, 2001}.issubset(seen)
            and {6, 7, 10}.issubset(gi_cots),
            "seen_ioas": sorted(seen),
            "gi_cots": sorted(gi_cots),
        }
    # Retain independent normal grid/status/maintenance checks, even when the
    # legacy fixed command counts intentionally do not apply to an explicit plan.
    normal = experiment["normal_regression"]["checks"]
    context = {k: v for k, v in normal.items() if k != "three_station_business_loop"}
    report = {
        "qc_version": VERSION,
        "run_id": manifest["run_id"],
        "run_code_version": manifest["code_version"],
        "evaluation_code_version": evaluator,
        "evaluation_code_dirty": evaluator_dirty,
        "scope": "Oracle_normal_schedule_acceptance_not_anomaly_labels",
        "configuration_integrity": integrity,
        "experiment_quality": experiment,
        "scheduled_business": business,
        "telemetry_and_initial_interrogation": telemetry,
        "normal_context": context,
        "normal_transport": transport,
        "passed": integrity
        and not evaluator_dirty
        and experiment["passed"]
        and transport["passed"]
        and business["passed"]
        and all(v["passed"] for v in telemetry.values())
        and all(v["passed"] for v in context.values()),
    }
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".schedule-qc-", dir=destination.parent) as temp:
        stage = Path(temp) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return report
