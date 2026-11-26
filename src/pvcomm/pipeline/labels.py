"""Offline Oracle labeling from retained evidence; never an M2 input path."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.config.power import power_document
from pvcomm.domain.labels import EventLabel, EvidenceRef, ServiceFinding
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-label-v3"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def record_identity(row: dict[str, Any]) -> str:
    return str(row["event_id"]) if "event_id" in row else f"{row['apdu_id']}:{row['object_index']}"


@dataclass
class LabelInputs:
    root: Path
    manifest: dict[str, Any]
    master: list[dict[str, Any]]
    terminal: list[dict[str, Any]]
    states: list[dict[str, Any]]
    objects: list[dict[str, Any]]
    report: dict[str, Any]
    report_path: Path
    hashes: dict[str, str]
    records: dict[str, tuple[str, dict[str, Any]]]

    def ref(self, row: dict[str, Any], *, online: bool = False) -> EvidenceRef:
        identity = record_identity(row)
        path, actual = self.records[identity]
        if actual != row:
            raise ValueError("record identity collision")
        tier: Literal["M1", "M2", "Oracle"] = (
            "M2"
            if path.startswith("observed/")
            else "M1"
            if path.startswith("parsed/")
            else "Oracle"
        )
        return EvidenceRef(
            path=path,
            sha256=self.hashes[path],
            record_id=identity,
            tier=tier,
            available_at=row.get("available_at"),
            role="online" if online else "retrospective",
        )

    def file_ref(self, path: Path) -> EvidenceRef:
        relative = path.relative_to(self.root).as_posix()
        return EvidenceRef(
            path=relative, sha256=self.hashes[relative], tier="Oracle", role="retrospective"
        )


def load_label_inputs(root: Path) -> LabelInputs:
    manifest = json.loads((root / "manifest.json").read_bytes())
    fault = root / "truth/fault/effective.json"
    if fault.exists():
        config = FaultScenarioConfig.model_validate_json(fault.read_bytes())
        if config.fault.fault_type == "A01":
            from pvcomm.qc.fault import VERSION as qc_version
        elif config.fault.fault_type == "A02":
            from pvcomm.qc.delay import VERSION as qc_version
        else:
            from pvcomm.qc.terminal import VERSION as qc_version
    else:
        from pvcomm.qc.schedule import VERSION as qc_version
    report_path = root / "qc" / qc_version / "report.json"
    report = json.loads(report_path.read_bytes())
    if report["run_id"] != manifest["run_id"]:
        raise ValueError("QC belongs to another run")
    groups = (
        list((root / "observed").glob("master_events*.jsonl")),
        list((root / "truth").glob("terminal_events*.jsonl")),
        list((root / "truth").glob("electrical_state*.jsonl")),
        [root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl"],
    )
    hashes = {}
    records: dict[str, tuple[str, dict[str, Any]]] = {}
    rows = []
    for paths in groups:
        group = []
        for path in paths:
            relative = path.relative_to(root).as_posix()
            hashes[relative] = file_sha256(path)
            for row in read_jsonl([path]):
                if row.get("run_id") != manifest["run_id"]:
                    raise ValueError("cross-run source evidence")
                identity = record_identity(row)
                if identity in records:
                    raise ValueError("duplicate source record identity")
                records[identity] = relative, row
                group.append(row)
        rows.append(group)
    files = [root / "manifest.json", root / "power_config.json", report_path]
    files += [root / c["path"] for c in manifest["captures"]]
    files += list((root / "truth/scenario").glob("*.json"))
    files += list((root / "truth/fault").glob("*.json*"))
    files += list((root / "truth/fault").glob("*.yaml"))
    for path in files:
        hashes[path.relative_to(root).as_posix()] = file_sha256(path)
    return LabelInputs(
        root, manifest, rows[0], rows[1], rows[2], rows[3], report, report_path, hashes, records
    )


def reception_findings(
    inputs: LabelInputs, station_id: str, period_s: float
) -> list[ServiceFinding]:
    """Reception freshness, not an unsupported claim about device sample age."""
    master = inputs.master
    began = [r for r in master if r["event_type"] == "master_business_started"]
    ended = [r for r in master if r["event_type"] == "master_business_finished"]
    if len(began) != 1 or len(ended) != 1:
        raise ValueError("labeling needs an unambiguous business observation interval")
    start, end = began[0]["available_at"], ended[0]["available_at"]
    # Existing master observation policy, not a newly relaxed experiment gate.
    budget = int((2 * period_s + 1) * 1e9)
    plans = [
        r
        for r in master
        if r["event_type"] == "master_maintenance_plan"
        and r.get("station_id") == station_id
        and r["available_at"] <= r["window_start_utc_ns"]
    ]
    exemptions = [
        (r["window_start_utc_ns"], r["window_end_utc_ns"] + int(r["recovery_deadline_s"] * 1e9), r)
        for r in plans
    ]
    findings: list[ServiceFinding] = []
    for ioa in (1001, 1002, 1003, 1008, 2001):
        samples = sorted(
            (
                r
                for r in master
                if r["event_type"] == "master_observed_point"
                and r.get("station_id") == station_id
                and r.get("ioa") == ioa
                and r.get("quality_valid") is True
                and r.get("value") is not None
                and r["available_at"] < end
            ),
            key=lambda r: r["available_at"],
        )
        previous = next((r for r in reversed(samples) if r["available_at"] <= start), None)
        future = [r for r in samples if r["available_at"] > start]
        for following in [*future, None]:
            expires = (previous["available_at"] if previous else start) + budget
            gap_end = following["available_at"] if following else end
            pieces: list[tuple[int, int, list[dict[str, Any]]]] = [
                (max(start, expires), gap_end, [])
            ]
            for left, right, plan in exemptions:
                revised = []
                for a, b, prior_plans in pieces:
                    if a < right and left < b:
                        if a < left:
                            revised.append((a, left, prior_plans + [plan]))
                        if right < b:
                            revised.append((right, b, prior_plans + [plan]))
                    else:
                        revised.append((a, b, prior_plans))
                pieces = revised
            for a, b, plan_refs in pieces:
                if a >= b:
                    continue
                refs = (
                    [inputs.ref(previous, online=True)]
                    if previous
                    else [inputs.ref(began[0], online=True)]
                )
                refs.extend(inputs.ref(r, online=True) for r in plan_refs)
                refs.append(inputs.ref(following if following else ended[0]))
                findings.append(
                    ServiceFinding(
                        finding_id=f"f_{station_id}_{ioa}_{len(findings):06d}",
                        kind="reception_freshness",
                        station_id=station_id,
                        ioa=ioa,
                        outcome="reception_freshness_expired",
                        service_violation=True,
                        label_status="valid",
                        start_utc_ns=a,
                        end_utc_ns=b,
                        observable_at_utc_ns=a,
                        recovery_utc_ns=b if following and b == gap_end else None,
                        recovery_censored=not (following and b == gap_end),
                        evidence=tuple(refs),
                    )
                )
            previous = following
    return findings


def command_findings(inputs: LabelInputs, station_id: str) -> list[ServiceFinding]:
    quality = inputs.report.get("experiment_quality", inputs.report.get("experiment", {}))
    assessments = quality.get("command_assessments", {}).get(station_id, [])
    commands = {
        r["event_id"]: r
        for r in inputs.master
        if r["event_type"] == "master_command_sent" and r.get("station_id") == station_id
    }
    if {r["command_event"] for r in assessments} != set(commands):
        raise ValueError("command assessment coverage is incomplete")
    station = next(
        s
        for s in power_document(inputs.root / "power_config.json").protocol.stations
        if s.station_id == station_id
    )
    findings: list[ServiceFinding] = []
    for assessment in assessments:
        command = commands[assessment["command_event"]]
        start = command["sent_utc_ns"]
        outcome = assessment["outcome"]
        violation = outcome in ("confirmation_timeout", "feedback_missing")
        known = assessment["transaction_evidence_valid"]
        if outcome == "feedback_missing" and assessment.get("setpoint_feedback_event"):
            acceptance = inputs.records.get(assessment.get("accept_event", ""))
            delivered_power = [
                r
                for r in inputs.master
                if acceptance is not None
                and r["event_type"] == "master_observed_point"
                and r.get("station_id") == station_id
                and r.get("ioa") == 1001
                and r.get("quality_valid") is True
                and r.get("value") is not None
                and acceptance[1]["ts_mono_ns"]
                <= r["ts_mono_ns"]
                < assessment["execution_end_mono_ns_exclusive"]
            ]
            if delivered_power:
                # Delivery occurred. An execution mismatch needs physical diagnosis;
                # it is not evidence of a missing communication feedback message.
                outcome, known, violation = "execution_result_uncertain", False, False
        refs = [inputs.ref(command, online=True)]
        for key in (
            "accept_event",
            "response_event",
            "setpoint_feedback_event",
            "power_feedback_event",
            "negative_response_event",
            "rejection_event",
        ):
            identity = assessment.get(key)
            if identity:
                refs.append(inputs.ref(inputs.records[identity][1]))
        status: Literal["valid", "uncertain", "invalid"] = "valid" if known else "uncertain"
        if outcome == "rejected":
            states = sorted(
                (
                    r
                    for r in inputs.states
                    if r["station_id"] == station_id
                    and r.get("source") == "pvlib_pvwatts_inverter_v02"
                    and r.get("model_mono_ns", 0) <= command["sent_mono_ns"]
                ),
                key=lambda r: r.get("model_mono_ns", 0),
            )
            state = states[-1] if states else None
            if not state or state["faulted"] or (state["enabled"] and state["connected"]):
                known, status = False, "uncertain"
            elif state:
                refs.append(inputs.ref(state))
        deadline = (
            start
            + int(
                command["deadline_ms"]
                if outcome == "confirmation_timeout"
                else command["execution_deadline_ms"]
            )
            * 1_000_000
        )
        wire = next(
            (r for r in inputs.objects if r["apdu_id"] == assessment.get("wire_apdu")), None
        )
        following = min(
            (
                r["sent_utc_ns"]
                for r in commands.values()
                if r["sent_utc_ns"] > start and r["ioa"] == command["ioa"]
            ),
            default=2**63 - 1,
        )
        # Business observation records establish that the deadline was observed.
        for key in ("wire_apdu", "wire_ack_apdu", "negative_ack_apdu"):
            matching = [r for r in inputs.objects if r["apdu_id"] == assessment.get(key)]
            if len(matching) == 1:
                refs.append(inputs.ref(matching[0], online=matching[0]["available_at"] <= deadline))
        coverage = max(inputs.master, key=lambda r: r["available_at"])
        refs.append(inputs.ref(coverage))
        recovery = None
        recovery_kind: (
            Literal["late_positive_confirmation", "late_negative_confirmation", "feedback"] | None
        ) = None
        execution_recovery = None
        if violation:
            if outcome == "confirmation_timeout":
                later = sorted(
                    (
                        r
                        for r in inputs.objects
                        if r.get("type_id") == 50
                        and r.get("cot") == 7
                        and not r.get("test_flag")
                        and r.get("ca") == station.common_address
                        and wire is not None
                        and r.get("flow_id") == wire.get("flow_id")
                        and r.get("src_ip") == wire.get("dst_ip")
                        and r.get("negative_confirm") in (True, False)
                        and r["available_at"] < following
                        and r.get("ioa") == command["ioa"]
                        and abs((r.get("value_num") or 0) - command["target_kw"]) <= 0.1
                        and r["available_at"] >= deadline
                    ),
                    key=lambda r: r["available_at"],
                )
                if len(later) == 1:
                    recovery = later[0]["available_at"]
                    recovery_kind = (
                        "late_negative_confirmation"
                        if later[0]["negative_confirm"]
                        else "late_positive_confirmation"
                    )
                    refs.append(inputs.ref(later[0]))
            else:
                feedback = {}
                acceptance = inputs.records.get(assessment.get("accept_event", ""))
                for ioa in (1006, 1001):
                    candidates = sorted(
                        (
                            r
                            for r in inputs.master
                            if r["event_type"] == "master_observed_point"
                            and r.get("station_id") == station_id
                            and r.get("ioa") == ioa
                            and r.get("quality_valid") is True
                            and r.get("value") is not None
                            and acceptance is not None
                            and acceptance[1]["ts_mono_ns"] <= r["ts_mono_ns"]
                            and r["available_at"] < following
                            and (ioa != 1006 or abs(r["value"] - command["target_kw"]) <= 0.1)
                        ),
                        key=lambda r: r["available_at"],
                    )
                    if candidates:
                        feedback[ioa] = candidates[0]
                if set(feedback) == {1001, 1006}:
                    recovery = max(r["available_at"] for r in feedback.values())
                    recovery_kind = "feedback"
                    refs.extend(inputs.ref(r) for r in feedback.values())
                    # Delivered measurements restore feedback service, not necessarily execution.
                    power = feedback[1001]
                    states = [
                        r
                        for r in inputs.states
                        if r["station_id"] == station_id
                        and r.get("source") == "pvlib_pvwatts_inverter_v02"
                        and command["sent_mono_ns"]
                        <= r.get("model_mono_ns", 0)
                        <= power["ts_mono_ns"]
                    ]
                    state = max(states, key=lambda r: r["model_mono_ns"]) if states else None
                    if (
                        state
                        and state.get("p_actual_kw") is not None
                        and (
                            abs(state.get("p_set_accepted_kw", -1) - command["target_kw"]) <= 0.1
                            and abs(
                                state["p_actual_kw"]
                                - min(command["target_kw"], state["p_avail_kw"])
                            )
                            <= 0.1
                            and abs(power["value"] - state["p_actual_kw"]) <= 0.1
                        )
                    ):
                        execution_recovery = recovery
                        refs.append(inputs.ref(state))
        else:
            completion = (
                assessment.get("negative_response_event")
                if outcome == "rejected"
                else assessment.get("power_feedback_event")
            )
            if known and completion:
                recovery = max(
                    inputs.records[identity][1]["available_at"]
                    for identity in (
                        completion,
                        assessment.get("setpoint_feedback_event") or completion,
                        assessment.get("response_event") or completion,
                    )
                )
        superseded = following if violation and recovery is None and following < 2**63 - 1 else None
        # No future completion or later same-value task can close the prior transaction.
        refs = list({(r.path, r.record_id, r.role): r for r in refs}.values())
        findings.append(
            ServiceFinding(
                finding_id=f"c_{len(findings):06d}",
                kind="command",
                station_id=station_id,
                task_id=command["task_id"],
                ioa=command["ioa"],
                outcome=outcome,
                service_violation=violation if known else None,
                label_status=status,
                start_utc_ns=deadline if violation else start,
                end_utc_ns=recovery if violation and recovery else superseded,
                observable_at_utc_ns=deadline if violation else None,
                recovery_utc_ns=recovery,
                recovery_censored=recovery is None,
                recovery_kind=recovery_kind,
                execution_recovery_utc_ns=execution_recovery,
                superseded_at_utc_ns=superseded,
                evidence=tuple(refs),
            )
        )
    return findings


def full_recovery(
    inputs: LabelInputs, station_id: str, after: int | None
) -> tuple[int | None, list[EvidenceRef]]:
    """A new successful GI with actual measurements, not first queued telemetry."""
    if after is None:
        return None, []
    site = next(
        s
        for s in power_document(inputs.root / "power_config.json").protocol.stations
        if s.station_id == station_id
    )
    address = inputs.manifest["network"]["addresses"][
        list(power_document(inputs.root / "power_config.json").protocol.stations).index(site)
    ]
    requests = sorted(
        (
            r
            for r in inputs.master
            if r["event_type"] == "master_interrogation_requested"
            and r.get("station_id") == station_id
            and r["available_at"] >= after
        ),
        key=lambda r: r["available_at"],
    )
    for request in requests:
        results = [
            r
            for r in inputs.master
            if r["event_type"] == "master_interrogation_result"
            and r.get("station_id") == station_id
            and r.get("dispatch_id") == request.get("dispatch_id")
            and r["available_at"] > request["available_at"]
            and r.get("success")
        ]
        if len(results) != 1:
            continue
        result = results[0]
        window = [
            r
            for r in inputs.objects
            if r.get("ca") == site.common_address
            and request["available_at"] <= r["available_at"] < result["available_at"]
            and not r.get("test_flag")
            and not r.get("negative_confirm")
        ]
        sent = [
            r
            for r in window
            if r.get("type_id") == 100 and r.get("cot") == 6 and r.get("dst_ip") == address
        ]
        if len(sent) != 1:
            continue
        responses = [
            r
            for r in window
            if r.get("src_ip") == address
            and r.get("flow_id") == sent[0].get("flow_id")
            and r["available_at"] >= sent[0]["available_at"]
        ]
        if not {7, 10}.issubset({r["cot"] for r in responses if r["type_id"] == 100}):
            continue
        if not {1001, 1002, 1003, 1006, 1008, 2001}.issubset(
            {r["ioa"] for r in responses if r["cot"] == 20}
        ):
            continue
        return max(r["available_at"] for r in responses), [
            inputs.ref(request),
            inputs.ref(result),
            inputs.ref(sent[0]),
            *(inputs.ref(r) for r in responses),
        ]
    return None, []


def build_labels(inputs: LabelInputs) -> list[EventLabel]:
    config = power_document(inputs.root / "power_config.json")
    report = inputs.report
    fault_path = inputs.root / "truth/fault/effective.json"
    fault = (
        FaultScenarioConfig.model_validate_json(fault_path.read_bytes())
        if fault_path.exists()
        else None
    )
    quality = report.get("experiment_quality", report.get("experiment", {}))
    quality_ok = bool(quality.get("passed"))
    rows: list[EventLabel] = []
    journal = read_jsonl([inputs.root / "truth/fault/events.jsonl"]) if fault else []
    for station in config.protocol.stations:
        target = fault is not None and station.station_id in fault.target_stations()
        findings = command_findings(inputs, station.station_id)
        findings.extend(reception_findings(inputs, station.station_id, station.telemetry_period_s))
        violation = (
            True
            if any(f.service_violation for f in findings)
            else None
            if any(f.service_violation is None for f in findings)
            else False
        )
        refs = [inputs.file_ref(inputs.report_path)]
        if target:
            refs.append(inputs.file_ref(fault_path))
            refs.append(inputs.file_ref(inputs.root / "truth/fault/events.jsonl"))
        actual = [
            r
            for r in journal
            if r["event_type"] == "apply_verified"
            and (r.get("after", {}).get("station_id") in (None, station.station_id))
        ]
        restored = [
            r
            for r in journal
            if r["event_type"] == "restore_verified"
            and (r.get("after", {}).get("station_id") in (None, station.station_id))
        ]
        if target and fault and fault.fault.fault_type == "A06":
            # Arming does not suppress feedback. The actual accepted command starts the hold.
            actual = [
                r
                for r in inputs.terminal
                if r.get("station_id") == station.station_id
                and r.get("fault_event_id") == fault.fault.event_id
                and r["event_type"] == "terminal_fault_feedback_hold"
            ]
            refs.extend(inputs.ref(r) for r in actual)
        fault_start = min((r["ts_event_utc_ns"] for r in actual), default=None) if target else None
        fault_end = max((r["ts_event_utc_ns"] for r in restored), default=None) if target else None
        fault_ok = report.get("fault_verification", {}).get("passed") if target else False
        # Endpoint loss/delay is transport evidence; application suppression alone is not.
        transport = (
            True
            if target
            and fault
            and fault.fault.fault_type in ("A01", "A02")
            and report.get("communication_effects", {}).get("passed")
            else None
        )
        effects = report.get("communication_effects", {})
        effect = effects.get("stations", {}).get(station.station_id, {}) if target else {}
        impact, impact_start, impact_end = None, None, None
        if target and fault:
            kind = fault.fault.fault_type
            if kind == "A01" and effect.get("withheld_evidence"):
                transport = impact = True
                impact_start, impact_end = effects["verified_blocked_interval_utc_ns"]
            elif kind == "A02":
                measured = [
                    r
                    for r in effects.get("directions", [])
                    if r["station_id"] == station.station_id and r["targeted"]
                ]
                if measured and all(r["passed"] for r in measured):
                    transport = impact = True
                    impact_start, impact_end = effects["windows_utc_ns"]["active"]
            elif kind in ("A04", "A05", "A06", "A08") and effect.get("passed"):
                impact = True
                impact_start, impact_end = effect["begin_utc_ns"], effect["end_utc_ns"]
                if kind == "A05":
                    waits = [
                        r
                        for r in inputs.terminal
                        if r.get("station_id") == station.station_id
                        and r.get("fault_event_id") == fault.fault.event_id
                        and r["event_type"]
                        in ("terminal_response_delay_started", "terminal_response_delay_finished")
                    ]
                    if len(waits) == 2:
                        impact_start = min(r["ts_event_utc_ns"] for r in waits)
                        impact_end = max(r["ts_event_utc_ns"] for r in waits)
                        refs.extend(inputs.ref(r) for r in waits)
                    else:
                        impact, impact_start, impact_end = None, None, None
        recovered, recovery_refs = full_recovery(inputs, station.station_id, fault_end)
        refs.extend(recovery_refs)
        if not target or (fault and fault.fault.fault_type in ("A04", "A05", "A06", "A08")):
            endpoints = quality.get("transport_observations", {})
            if endpoints and all(r["same_bytes"] for r in endpoints.values()):
                # No demonstrated transport degradation, not a proof of zero latency.
                transport = None
        invalid = not quality_ok or (target and not fault_ok)
        rows.append(
            EventLabel(
                run_id=inputs.manifest["run_id"],
                anomaly_id=f"e_{len(rows):06d}",
                station_id=station.station_id,
                region_id=station.region_id,
                scope=fault.fault.scope.kind if target and fault else "station",
                fault_applied=bool(fault_ok) if target else False,
                fault_type=(fault.fault.fault_type,) if target and fault else (),
                fault_start_utc_ns=fault_start,
                fault_end_utc_ns=fault_end,
                transport_impact=transport,
                communication_impact=impact,
                communication_impact_start_utc_ns=impact_start,
                communication_impact_end_utc_ns=impact_end,
                recovery_complete_utc_ns=recovered,
                recovery_censored=recovered is None,
                service_violation=None if invalid else violation,
                observable_anomaly=None if invalid or violation is None else violation,
                operating_context=tuple(
                    sorted(
                        {
                            "command"
                            if any(f.kind == "command" for f in findings)
                            else "periodic_telemetry"
                        }
                        | (
                            {"authorized_maintenance"}
                            if any(
                                r["event_type"] == "master_maintenance_plan"
                                and r.get("station_id") == station.station_id
                                for r in inputs.master
                            )
                            else set()
                        )
                    )
                ),
                label_status="invalid"
                if invalid
                else "uncertain"
                if violation is None
                else "valid",
                findings=tuple(findings),
                evidence=tuple(refs),
                uncertainty_reasons=(
                    ("transport_not_fully_characterized",) if transport is None else ()
                )
                + (("experiment_quality_or_injection_invalid",) if invalid else ()),
            )
        )
    return rows


def label_run(root: Path) -> dict[str, Any]:
    destination = root / "labels" / VERSION
    if destination.exists():
        raise FileExistsError(f"labels already exist: {destination}")
    inputs = load_label_inputs(root)
    rows = build_labels(inputs)
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
    repository = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    result = dict(
        label_version=VERSION,
        run_id=inputs.manifest["run_id"],
        source_hashes=inputs.hashes,
        run_code_version=inputs.manifest["code_version"],
        evaluation_code_version=sha,
        evaluation_code_dirty=dirty,
        rows=len(rows),
        scope="Oracle_labels_never_M2_features",
    )
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".labels-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir()
        serialized = [row.model_dump(mode="json") for row in rows]
        (stage / "event_labels.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in serialized), encoding="utf-8"
        )
        # JSON payload preserves nested evidence exactly; indexed layers have explicit Arrow types.
        schema = pa.schema(
            [
                pa.field("run_id", pa.string(), nullable=False),
                pa.field("station_id", pa.string(), nullable=False),
                pa.field("service_violation", pa.bool_()),
                pa.field("label_status", pa.string(), nullable=False),
                pa.field("payload_json", pa.string(), nullable=False),
            ],
            metadata={b"scope": b"Oracle", b"pvcomm_schema": b"0.4-label.3", b"utc_unit": b"ns"},
        )
        projection = [
            dict(
                run_id=r["run_id"],
                station_id=r["station_id"],
                service_violation=r["service_violation"],
                label_status=r["label_status"],
                payload_json=json.dumps(r, sort_keys=True),
            )
            for r in serialized
        ]
        table = pa.Table.from_pylist(projection, schema=schema)
        pq.write_table(table, stage / "event_labels.parquet", compression="zstd")
        if not pq.read_table(stage / "event_labels.parquet").equals(table, check_metadata=True):
            raise ValueError("label Arrow roundtrip mismatch")
        result["label_sha256"] = file_sha256(stage / "event_labels.jsonl")
        result["parquet_sha256"] = file_sha256(stage / "event_labels.parquet")
        (stage / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return result
