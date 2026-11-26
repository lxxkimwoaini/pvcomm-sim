"""Independent label audits from raw event/PCAP-derived evidence and deadlines."""

from __future__ import annotations

import importlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.config.power import power_document
from pvcomm.domain.labels import EventLabel, ServiceFinding
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import LabelInputs, file_sha256, load_label_inputs

VERSION = "v04-label-qc-v4"


def audit_command_interval(
    inputs: LabelInputs,
    finding: ServiceFinding,
    command: dict[str, Any],
    wire: dict[str, Any],
    boundary: int,
) -> bool:
    """Independently verify recovery attribution; feedback is not physical execution."""
    start = command["sent_utc_ns"]
    deadline = (
        start
        + command[
            "deadline_ms" if finding.outcome == "confirmation_timeout" else "execution_deadline_ms"
        ]
        * 1_000_000
    )
    recovery, execution, kind = None, None, None
    supporting = []
    if finding.outcome == "confirmation_timeout":
        candidates = [
            row
            for row in inputs.objects
            if row.get("type_id") == 50
            and row.get("cot") == 7
            and row.get("ca") == wire.get("ca")
            and row.get("ioa") == command["ioa"]
            and row.get("src_ip") == wire.get("dst_ip")
            and row.get("flow_id") == wire.get("flow_id")
            and not row.get("test_flag")
            and type(row.get("negative_confirm")) is bool
            and row.get("value_num") is not None
            and abs(row["value_num"] - command["target_kw"]) <= 0.1
            and deadline <= row["available_at"] < boundary
        ]
        if len(candidates) == 1:
            row = candidates[0]
            recovery = row["available_at"]
            kind = (
                "late_negative_confirmation"
                if row["negative_confirm"]
                else "late_positive_confirmation"
            )
            supporting.append(row)
    elif finding.outcome == "feedback_missing":
        acceptances = [
            r
            for r in inputs.terminal
            if r["event_type"] == "terminal_command_accepted"
            and r.get("station_id") == finding.station_id
            and r.get("ioa") == command["ioa"]
            and r.get("target_kw") is not None
            and abs(r["target_kw"] - command["target_kw"]) <= 0.1
            and command["sent_mono_ns"]
            <= r["ts_mono_ns"]
            < min(
                command["sent_mono_ns"] + command["deadline_ms"] * 1_000_000,
                command["sent_mono_ns"] + boundary - start,
            )
        ]
        samples: dict[int, dict[str, Any]] = {}
        for row in sorted(inputs.master, key=lambda r: r["available_at"]):
            if (
                row["event_type"] == "master_observed_point"
                and row.get("station_id") == finding.station_id
                and row.get("ioa") in (1001, 1006)
                and row.get("quality_valid") is True
                and row.get("value") is not None
                and len(acceptances) == 1
                and row["ts_mono_ns"] >= acceptances[0]["ts_mono_ns"]
                and row["available_at"] < boundary
                and (row["ioa"] != 1006 or abs(row["value"] - command["target_kw"]) <= 0.1)
            ):
                samples.setdefault(row["ioa"], row)
        if len(samples) == 2:
            recovery = max(row["available_at"] for row in samples.values())
            kind = "feedback"
            supporting.extend(samples.values())
            power = samples[1001]
            states = [
                r
                for r in inputs.states
                if r["station_id"] == finding.station_id
                and r.get("source") == "pvlib_pvwatts_inverter_v02"
                and command["sent_mono_ns"] <= r.get("model_mono_ns", 0) <= power["ts_mono_ns"]
            ]
            if states:
                state = max(states, key=lambda r: r["model_mono_ns"])
                if (
                    state.get("p_actual_kw") is not None
                    and abs(state.get("p_set_accepted_kw", -1) - command["target_kw"]) <= 0.1
                    and abs(state["p_actual_kw"] - min(command["target_kw"], state["p_avail_kw"]))
                    <= 0.1
                    and abs(power["value"] - state["p_actual_kw"]) <= 0.1
                ):
                    execution = recovery
                    supporting.append(state)
    else:
        return finding.recovery_kind is None and finding.execution_recovery_utc_ns is None
    superseded = boundary if recovery is None and boundary < 2**63 - 1 else None
    reference_ids = {r.record_id for r in finding.evidence if r.role == "retrospective"}
    return bool(
        finding.recovery_utc_ns == recovery
        and finding.recovery_kind == kind
        and finding.execution_recovery_utc_ns == execution
        and finding.superseded_at_utc_ns == superseded
        and finding.end_utc_ns == (recovery if recovery is not None else superseded)
        and finding.recovery_censored == (recovery is None)
        and all(
            (str(r["event_id"]) if "event_id" in r else f"{r['apdu_id']}:{r['object_index']}")
            in reference_ids
            for r in supporting
        )
    )


def audit_diagnostic_layers(inputs: LabelInputs, label: EventLabel) -> bool:
    """Check source events, independent mechanism results and complete new GI evidence."""
    path = inputs.root / "truth/fault/effective.json"
    fault = FaultScenarioConfig.model_validate_json(path.read_bytes()) if path.exists() else None
    target = fault is not None and label.station_id in fault.target_stations()
    start, end = None, None
    transport, impact, begin, finish = None, None, None, None
    if target and fault:
        journal = [
            json.loads(line) for line in (path.parent / "events.jsonl").read_text().splitlines()
        ]
        own = [
            r for r in journal if r.get("after", {}).get("station_id") in (None, label.station_id)
        ]
        starts = [r["ts_event_utc_ns"] for r in own if r["event_type"] == "apply_verified"]
        ends = [r["ts_event_utc_ns"] for r in own if r["event_type"] == "restore_verified"]
        if fault.fault.fault_type == "A06":
            starts = [
                r["ts_event_utc_ns"]
                for r in inputs.terminal
                if r.get("station_id") == label.station_id
                and r.get("fault_event_id") == fault.fault.event_id
                and r["event_type"] == "terminal_fault_feedback_hold"
            ]
        start, end = min(starts, default=None), max(ends, default=None)
        facts = inputs.report.get("communication_effects", {})
        if fault.fault.fault_type == "A01":
            if facts.get("stations", {}).get(label.station_id, {}).get("withheld_evidence"):
                transport = impact = True
                begin, finish = facts["verified_blocked_interval_utc_ns"]
        elif fault.fault.fault_type == "A02":
            own_directions = [
                r
                for r in facts.get("directions", [])
                if r["station_id"] == label.station_id and r["targeted"]
            ]
            if own_directions and all(r["passed"] for r in own_directions):
                transport = impact = True
                begin, finish = facts["windows_utc_ns"]["active"]
        else:
            effect = facts.get("stations", {}).get(label.station_id, {})
            if effect.get("passed"):
                impact = True
                begin, finish = effect["begin_utc_ns"], effect["end_utc_ns"]
                if fault.fault.fault_type == "A05":
                    starts = [
                        r["ts_event_utc_ns"]
                        for r in inputs.terminal
                        if r.get("station_id") == label.station_id
                        and r.get("fault_event_id") == fault.fault.event_id
                        and r["event_type"] == "terminal_response_delay_started"
                    ]
                    stops = [
                        r["ts_event_utc_ns"]
                        for r in inputs.terminal
                        if r.get("station_id") == label.station_id
                        and r.get("fault_event_id") == fault.fault.event_id
                        and r["event_type"] == "terminal_response_delay_finished"
                    ]
                    if len(starts) == len(stops) == 1:
                        begin, finish = starts[0], stops[0]
                    else:
                        impact, begin, finish = None, None, None
    ok = (label.fault_start_utc_ns, label.fault_end_utc_ns) == (start, end)
    ok &= (
        label.transport_impact,
        label.communication_impact,
        label.communication_impact_start_utc_ns,
        label.communication_impact_end_utc_ns,
    ) == (transport, impact, begin, finish)
    # Independently enumerate completed GIs in the actual post-restoration trace.
    site = next(
        s
        for s in power_document(inputs.root / "power_config.json").protocol.stations
        if s.station_id == label.station_id
    )
    address = inputs.manifest["network"]["addresses"][
        list(power_document(inputs.root / "power_config.json").protocol.stations).index(site)
    ]
    completions = []
    for result in inputs.master:
        if (
            end is None
            or result["event_type"] != "master_interrogation_result"
            or result.get("station_id") != label.station_id
            or not result.get("success")
        ):
            continue
        requests = [
            r
            for r in inputs.master
            if r["event_type"] == "master_interrogation_requested"
            and r.get("station_id") == label.station_id
            and r.get("dispatch_id") == result.get("dispatch_id")
            and end <= r["available_at"] < result["available_at"]
        ]
        if len(requests) != 1:
            continue
        objects = [
            r
            for r in inputs.objects
            if r.get("ca") == site.common_address
            and requests[0]["available_at"] <= r["available_at"] < result["available_at"]
            and not r.get("test_flag")
            and not r.get("negative_confirm")
        ]
        commands = [
            r for r in objects if r["type_id"] == 100 and r["cot"] == 6 and r["dst_ip"] == address
        ]
        if len(commands) != 1:
            continue
        returned = [
            r
            for r in objects
            if r["flow_id"] == commands[0]["flow_id"]
            and r["src_ip"] == address
            and r["available_at"] >= commands[0]["available_at"]
        ]
        flags = {r["cot"] for r in returned if r["type_id"] == 100}
        measurements = {r["ioa"] for r in returned if r["cot"] == 20}
        if {7, 10} <= flags and {1001, 1002, 1003, 1006, 1008, 2001} <= measurements:
            completions.append(max(r["available_at"] for r in returned))
    recovery = min(completions, default=None)
    return bool(
        ok
        and label.recovery_complete_utc_ns == recovery
        and label.recovery_censored == (recovery is None)
    )


def audit_command_finding(inputs: LabelInputs, finding: ServiceFinding) -> bool:
    """No audit_commands / label producer call: independently reconstruct the transaction."""
    commands = [
        r
        for r in inputs.master
        if r["event_type"] == "master_command_sent"
        and r.get("station_id") == finding.station_id
        and r.get("task_id") == finding.task_id
    ]
    if len(commands) != 1:
        return False
    command = commands[0]
    start = command["sent_utc_ns"]
    ack_deadline = start + command["deadline_ms"] * 1_000_000
    execution_deadline = start + command["execution_deadline_ms"] * 1_000_000
    site = next(
        s
        for s in power_document(inputs.root / "power_config.json").protocol.stations
        if s.station_id == finding.station_id
    )
    address = inputs.manifest["network"]["addresses"][
        list(power_document(inputs.root / "power_config.json").protocol.stations).index(site)
    ]
    other = [
        r["sent_utc_ns"]
        for r in inputs.master
        if r["event_type"] == "master_command_sent"
        and r.get("station_id") == finding.station_id
        and r.get("ioa") == command["ioa"]
        and r["sent_utc_ns"] > start
    ]
    boundary = min(other, default=2**63 - 1)
    matching = [
        r
        for r in inputs.objects
        if r.get("type_id") == 50
        and r.get("ca") == site.common_address
        and r.get("ioa") == command["ioa"]
        and not r.get("test_flag")
        and r.get("value_num") is not None
        and abs(r["value_num"] - command["target_kw"]) <= 0.1
    ]
    wire = [
        r
        for r in matching
        if r.get("cot") == 6
        and r.get("dst_ip") == address
        and start <= r["available_at"] < min(ack_deadline, boundary)
    ]
    if len(wire) != 1:
        return finding.label_status == "uncertain" and finding.service_violation is None
    if not audit_command_interval(inputs, finding, command, wire[0], boundary):
        return False
    acknowledgments = [
        r
        for r in matching
        if r.get("cot") == 7
        and r.get("src_ip") == address
        and r["flow_id"] == wire[0]["flow_id"]
        and wire[0]["available_at"] <= r["available_at"] < min(ack_deadline, boundary)
    ]
    coverage = max(r["available_at"] for r in inputs.master)
    if finding.outcome == "confirmation_timeout":
        return (
            not acknowledgments
            and coverage >= ack_deadline
            and boundary >= ack_deadline
            and finding.start_utc_ns == ack_deadline
            and finding.observable_at_utc_ns == ack_deadline
            and finding.service_violation is True
        )
    if len(acknowledgments) != 1:
        return finding.label_status == "uncertain" and finding.service_violation is None
    ack = acknowledgments[0]
    terminal = [
        r
        for r in inputs.terminal
        if r.get("station_id") == finding.station_id
        and r.get("ioa") == command["ioa"]
        and r.get("common_address") == site.common_address
        and r.get("target_kw") is not None
        and abs(r["target_kw"] - command["target_kw"]) <= 0.1
        and command["sent_mono_ns"]
        <= r["ts_mono_ns"]
        < command["sent_mono_ns"] + command["deadline_ms"] * 1_000_000
    ]
    if finding.outcome == "rejected":
        rejection = [r for r in terminal if r["event_type"] == "terminal_command_rejected"]
        states = [
            r
            for r in inputs.states
            if r["station_id"] == finding.station_id
            and r.get("source") == "pvlib_pvwatts_inverter_v02"
            and r["model_mono_ns"] <= command["sent_mono_ns"]
        ]
        state = max(states, key=lambda r: r["model_mono_ns"]) if states else None
        return bool(
            ack.get("negative_confirm") is True
            and len(rejection) == 1
            and state
            and not state["faulted"]
            and (not state["enabled"] or not state["connected"])
            and finding.service_violation is False
        )
    accepted = [r for r in terminal if r["event_type"] == "terminal_command_accepted"]
    if ack.get("negative_confirm") or len(accepted) != 1:
        return finding.label_status == "uncertain" and finding.service_violation is None
    feedback = set()
    for r in inputs.master:
        if (
            r["event_type"] != "master_observed_point"
            or r.get("station_id") != finding.station_id
            or r.get("quality_valid") is not True
            or r.get("value") is None
            or not accepted[0]["ts_mono_ns"] <= r["ts_mono_ns"]
            or not r["available_at"] < min(execution_deadline, boundary)
        ):
            continue
        if r.get("ioa") == 1006 and abs(r["value"] - command["target_kw"]) <= 0.1:
            feedback.add(1006)
        if r.get("ioa") == 1001:
            states = [
                s
                for s in inputs.states
                if s["station_id"] == finding.station_id
                and s.get("source") == "pvlib_pvwatts_inverter_v02"
                and s["model_mono_ns"] <= r["ts_mono_ns"]
            ]
            state = max(states, key=lambda s: s["model_mono_ns"]) if states else None
            if state and abs(r["value"] - min(command["target_kw"], state["p_avail_kw"])) <= 0.1:
                feedback.add(1001)
    if finding.outcome == "accepted_complete":
        return feedback == {1001, 1006} and finding.service_violation is False
    if finding.outcome == "feedback_missing":
        return (
            feedback != {1001, 1006}
            and coverage >= execution_deadline
            and boundary >= execution_deadline
            and finding.service_violation is True
            and finding.start_utc_ns == execution_deadline
            and finding.observable_at_utc_ns == execution_deadline
        )
    return finding.label_status == "uncertain" and finding.service_violation is None


def expected_freshness(
    inputs: LabelInputs, station_id: str, period: float
) -> set[tuple[int, int, int]]:
    """Independent interval sweep with authorized maintenance clipped from expiry gaps."""
    events = inputs.master
    start = next(r["available_at"] for r in events if r["event_type"] == "master_business_started")
    end = next(r["available_at"] for r in events if r["event_type"] == "master_business_finished")
    grace = [
        (r["window_start_utc_ns"], r["window_end_utc_ns"] + r["recovery_deadline_s"] * 10**9)
        for r in events
        if r["event_type"] == "master_maintenance_plan"
        and r.get("station_id") == station_id
        and r["available_at"] <= r["window_start_utc_ns"]
    ]
    result = set()
    for ioa in (1001, 1002, 1003, 1008, 2001):
        times = sorted(
            {
                r["available_at"]
                for r in events
                if r["event_type"] == "master_observed_point"
                and r.get("station_id") == station_id
                and r.get("ioa") == ioa
                and r.get("quality_valid") is True
                and r.get("value") is not None
                and r["available_at"] < end
            }
        )
        last = max((t for t in times if t <= start), default=start)
        for until in [*(t for t in times if t > start), end]:
            left = max(start, last + int((2 * period + 1) * 1e9))
            breakpoints = sorted(
                {left, until} | {v for interval in grace for v in interval if left < v < until}
            )
            for a, b in zip(breakpoints, breakpoints[1:], strict=False):
                if a < b and a >= left and b <= until and not any(x <= a < y for x, y in grace):
                    result.add((ioa, a, b))
            last = until
    return result


def audit_label_records(inputs: LabelInputs, labels: list[EventLabel]) -> dict[str, Any]:
    config = power_document(inputs.root / "power_config.json")
    sites = {s.station_id: s for s in config.protocol.stations}
    fault_path = inputs.root / "truth/fault/effective.json"
    fault = (
        FaultScenarioConfig.model_validate_json(fault_path.read_bytes())
        if fault_path.exists()
        else None
    )
    identities = (
        len(labels) == len(sites)
        and {r.station_id for r in labels} == set(sites)
        and len({r.anomaly_id for r in labels}) == len(labels)
        and all(r.run_id == inputs.manifest["run_id"] for r in labels)
    )
    references, causal, commands, freshness, layers = True, True, True, True, True
    diagnostics = True
    evidence_coverage = True
    for label in labels:
        if label.station_id not in sites:
            continue
        diagnostics &= audit_diagnostic_layers(inputs, label)
        evidence_coverage &= any(
            ref.path == inputs.report_path.relative_to(inputs.root).as_posix()
            and ref.role == "retrospective"
            for ref in label.evidence
        )
        for ref in (*label.evidence, *(r for f in label.findings for r in f.evidence)):
            path = (inputs.root / ref.path).resolve()
            references &= path.is_relative_to(inputs.root.resolve()) and path.is_file()
            references &= inputs.hashes.get(ref.path) == ref.sha256
            if ref.record_id is not None:
                actual = inputs.records.get(ref.record_id)
                references &= bool(actual and actual[0] == ref.path)
                if actual:
                    references &= actual[1].get("available_at") == ref.available_at
            expected_tier = (
                "M2"
                if ref.path.startswith("observed/")
                else "M1"
                if ref.path.startswith("parsed/")
                else "Oracle"
            )
            references &= ref.tier == expected_tier
        for finding in label.findings:
            online = [
                inputs.records[ref.record_id][1]
                for ref in finding.evidence
                if ref.role == "online" and ref.record_id in inputs.records
            ]
            evidence_coverage &= len(
                {(r.path, r.record_id, r.role) for r in finding.evidence}
            ) == len(finding.evidence)
            if finding.kind == "command":
                evidence_coverage &= any(
                    r["event_type"] == "master_command_sent"
                    and r.get("station_id") == finding.station_id
                    and r.get("task_id") == finding.task_id
                    for r in online
                    if "event_type" in r
                )
                if finding.label_status == "valid":
                    evidence_coverage &= any(
                        ref.path.startswith("parsed/") and ref.record_id in inputs.records
                        for ref in finding.evidence
                    )
            else:
                evidence_coverage &= any(
                    r["event_type"] == "master_business_started"
                    or (
                        r["event_type"] == "master_observed_point"
                        and r.get("station_id") == finding.station_id
                        and r.get("ioa") == finding.ioa
                        and r.get("quality_valid") is True
                        and r.get("value") is not None
                    )
                    for r in online
                    if "event_type" in r
                )
            if finding.service_violation:
                causal &= finding.observable_at_utc_ns is not None
                causal &= all(
                    ref.tier != "Oracle"
                    and ref.available_at is not None
                    and finding.observable_at_utc_ns is not None
                    and ref.available_at <= finding.observable_at_utc_ns
                    for ref in finding.evidence
                    if ref.role == "online"
                )
            if finding.kind == "command":
                commands &= audit_command_finding(inputs, finding)
        expected_tasks = {
            r["task_id"]
            for r in inputs.master
            if r["event_type"] == "master_command_sent" and r.get("station_id") == label.station_id
        }
        command_rows = [f for f in label.findings if f.kind == "command"]
        commands &= {f.task_id for f in command_rows} == expected_tasks and len(
            command_rows
        ) == len(expected_tasks)
        stale = [f for f in label.findings if f.kind == "reception_freshness"]
        actual_stale = {(f.ioa, f.start_utc_ns, f.end_utc_ns) for f in stale}
        freshness &= actual_stale == expected_freshness(
            inputs, label.station_id, sites[label.station_id].telemetry_period_s
        )
        freshness &= len(stale) == len(actual_stale)
        freshness &= all(
            f.service_violation is True and f.observable_at_utc_ns == f.start_utc_ns for f in stale
        )
        for finding in stale:
            received_at_end = any(
                r["event_type"] == "master_observed_point"
                and r.get("station_id") == label.station_id
                and r.get("ioa") == finding.ioa
                and r.get("quality_valid") is True
                and r.get("value") is not None
                and r["available_at"] == finding.end_utc_ns
                for r in inputs.master
            )
            freshness &= finding.recovery_utc_ns == (
                finding.end_utc_ns if received_at_end else None
            ) and finding.recovery_censored == (not received_at_end)
        target = fault is not None and label.station_id in fault.target_stations()
        expected_applied = (
            bool(inputs.report.get("fault_verification", {}).get("passed")) if target else False
        )
        layers &= label.fault_applied is expected_applied
        layers &= label.fault_type == ((fault.fault.fault_type,) if fault and target else ())
        layers &= label.scope == (fault.fault.scope.kind if fault and target else "station")
        # An invalid experiment is not a negative business example.
        quality = inputs.report.get("experiment_quality", {})
        if not quality.get("passed"):
            layers &= label.label_status == "invalid" and label.service_violation is None
    checks = dict(
        identity_and_coverage=bool(identities),
        evidence_references=bool(references),
        required_evidence_coverage=bool(evidence_coverage),
        online_causality=bool(causal),
        command_deadlines_and_evidence=bool(commands),
        freshness_and_maintenance=bool(freshness),
        independent_layers=bool(layers),
        diagnostic_intervals_and_full_recovery=bool(diagnostics),
    )
    return dict(checks=checks, passed=all(checks.values()))


def qc_labels(root: Path) -> dict[str, Any]:
    destination = root / "qc" / VERSION
    if destination.exists():
        raise FileExistsError(f"label QC already exists: {destination}")
    inputs = load_label_inputs(root)
    folder = root / "labels" / LABEL_VERSION
    metadata = json.loads((folder / "manifest.json").read_bytes())
    labels = [
        EventLabel.model_validate_json(line)
        for line in (folder / "event_labels.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    audit = audit_label_records(inputs, labels)
    hashes_ok = (
        metadata["source_hashes"] == inputs.hashes
        and metadata["run_id"] == inputs.manifest["run_id"]
        and not metadata["evaluation_code_dirty"]
        and metadata["label_sha256"] == file_sha256(folder / "event_labels.jsonl")
        and metadata["parquet_sha256"] == file_sha256(folder / "event_labels.parquet")
    )
    table = importlib.import_module("pyarrow.parquet").read_table(folder / "event_labels.parquet")
    projection = table.to_pylist()
    arrow_ok = table.schema.metadata.get(b"scope") == b"Oracle" and len(projection) == len(labels)
    for actual, label in zip(projection, labels, strict=True):
        raw = label.model_dump(mode="json")
        arrow_ok &= json.loads(actual["payload_json"]) == raw
        arrow_ok &= all(
            actual[field] == raw[field]
            for field in ("run_id", "station_id", "service_violation", "label_status")
        )
    result = dict(
        qc_version=VERSION,
        run_id=inputs.manifest["run_id"],
        label_version=LABEL_VERSION,
        label_sha256=file_sha256(folder / "event_labels.jsonl"),
        label_manifest_sha256=file_sha256(folder / "manifest.json"),
        evidence_integrity=hashes_ok,
        arrow_roundtrip=arrow_ok,
        **audit,
    )
    repository = Path(__file__).resolve().parents[3]
    result["evaluation_code_version"] = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    result["evaluation_code_dirty"] = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    result["passed"] &= hashes_ok and arrow_ok and not result["evaluation_code_dirty"]
    result["valid_labels"] = sum(r.label_status == "valid" for r in labels)
    result["uncertain_labels"] = sum(r.label_status == "uncertain" for r in labels)
    result["invalid_labels"] = sum(r.label_status == "invalid" for r in labels)
    result["service_violation_stations"] = [r.station_id for r in labels if r.service_violation]
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".label-qc-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return result
