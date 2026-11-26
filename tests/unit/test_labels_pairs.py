"""Synthetic unit fixtures only: these events are not simulation evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.fault_scenes import a01_scenario, terminal_scenario
from pvcomm.config.faults import StationScope
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.pairs import PairConfig, development_pair
from pvcomm.domain.labels import EventLabel, EvidenceRef
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.pipeline.labels import build_labels, file_sha256, load_label_inputs
from pvcomm.qc.commands import audit_commands
from pvcomm.qc.labels import audit_label_records
from pvcomm.qc.schedule import VERSION as SCHEDULE_QC_VERSION


def fixture_inputs(root: Path, mode: str = "complete", gap: bool = False) -> Any:
    scene = normal_scenario("N04")
    simulation = scene.simulation
    protocol = simulation.protocol
    run_id = "unit_fixture_only"
    master: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []

    def event(kind: str, seconds: float, **payload: Any) -> dict[str, Any]:
        return dict(
            run_id=run_id,
            event_id=f"fixture_{len(master) + len(terminal) + len(states):06d}_{kind}_{seconds}",
            event_type=kind,
            available_at=int(seconds * 1e9),
            ts_event_utc_ns=int(seconds * 1e9),
            ts_mono_ns=int(seconds * 1e9),
            **payload,
        )

    master.append(event("master_business_started", 10))
    for site in protocol.stations:
        for sec in range(0, 100):
            for ioa in (1001, 1002, 1003, 1008, 2001):
                if not (gap and site.station_id == "PV01" and 30 <= sec < 45):
                    master.append(
                        event(
                            "master_observed_point",
                            sec,
                            station_id=site.station_id,
                            ioa=ioa,
                            value=4.0 if ioa == 1001 else 1.0,
                            quality_valid=True,
                        )
                    )
        states.append(
            event(
                "electrical_state",
                19,
                station_id=site.station_id,
                source="pvlib_pvwatts_inverter_v02",
                model_mono_ns=19_000_000_000,
                p_avail_kw=4.0,
                enabled=mode != "reject",
                connected=True,
                faulted=False,
            )
        )
    command = event(
        "master_command_sent",
        20,
        station_id="PV01",
        task_id="transaction_1",
        ioa=3001,
        target_kw=20.0,
        deadline_ms=3000,
        execution_deadline_ms=10000,
        sent_utc_ns=20_000_000_000,
        sent_mono_ns=20_000_000_000,
    )
    master.append(command)
    site = protocol.stations[0]
    for cot, seconds in ((6, 20.001), (7, 20.003)):
        if mode == "timeout" and cot == 7:
            continue
        objects.append(
            dict(
                run_id=run_id,
                apdu_id=f"wire_{cot}",
                object_index=0,
                ca=site.common_address,
                ioa=3001,
                type_id=50,
                cot=cot,
                src_ip=site.address if cot == 7 else "10.204.0.1",
                dst_ip=site.address if cot == 6 else "10.204.0.1",
                flow_id=1,
                value_num=20.0,
                available_at=int(seconds * 1e9),
                negative_confirm=mode == "reject" and cot == 7,
                test_flag=False,
            )
        )
    if mode != "timeout":
        terminal.append(
            event(
                "terminal_command_rejected" if mode == "reject" else "terminal_command_accepted",
                20.002,
                station_id="PV01",
                ioa=3001,
                common_address=site.common_address,
                target_kw=20.0,
            )
        )
        master.append(
            event(
                "master_command_response",
                20.004,
                station_id="PV01",
                task_id="transaction_1",
                ioa=3001,
                target_kw=20.0,
                positive_confirmation=mode != "reject",
            )
        )
    if mode not in ("missing_feedback", "reject", "timeout"):
        master.append(
            event(
                "master_observed_point",
                20.005,
                station_id="PV01",
                ioa=1006,
                value=20.0,
                quality_valid=True,
            )
        )
    master.append(event("master_business_finished", 100))
    report = dict(
        run_id=run_id,
        passed=True,
        experiment_quality=dict(
            passed=True,
            command_assessments={
                s.station_id: audit_commands(
                    master,
                    terminal,
                    objects,
                    s.station_id,
                    s.common_address,
                    s.address,
                    lambda target, _: min(target, 4.0),
                )
                for s in protocol.stations
            },
        ),
    )
    report_path = root / "qc" / SCHEDULE_QC_VERSION / "report.json"
    paths = (
        (root / "observed/master_events_master.jsonl", master),
        (root / "truth/terminal_events_test.jsonl", terminal),
        (root / "truth/electrical_state_test.jsonl", states),
        (root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl", objects),
    )
    for path, rows in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    (root / "power_config.json").write_text(simulation.model_dump_json(), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            dict(
                run_id=run_id,
                code_version="fixture",
                captures=[],
                network=dict(addresses=[s.address for s in protocol.stations]),
            )
        ),
        encoding="utf-8",
    )
    return load_label_inputs(root)


@pytest.mark.parametrize(
    "mode,violation",
    [("complete", False), ("reject", False), ("timeout", True), ("missing_feedback", True)],
)
def test_label_business_results_are_independently_audited(
    tmp_path: Path, mode: str, violation: bool
) -> None:
    inputs = fixture_inputs(tmp_path, mode)
    rows = build_labels(inputs)
    assert rows[0].service_violation is violation
    assert all(r.fault_applied is False for r in rows)
    assert audit_label_records(inputs, rows)["passed"]
    if mode == "complete":
        assert rows[0].findings[0].outcome == "accepted_complete"  # Pactual=4 < target=20 is legal.
    if violation:
        finding = rows[0].findings[0]
        assert finding.start_utc_ns == (23 if mode == "timeout" else 30) * 10**9


@pytest.mark.parametrize(
    "mutation",
    ["deadline", "cross_run", "hash", "missing", "tier", "no_finding_evidence", "no_qc_evidence"],
)
def test_independent_label_qc_rejects_corrupt_evidence(tmp_path: Path, mutation: str) -> None:
    inputs = fixture_inputs(tmp_path, "timeout")
    rows = build_labels(inputs)
    raw = rows[0].model_dump(mode="json")
    if mutation == "deadline":
        raw["findings"][0]["start_utc_ns"] -= 1
        raw["findings"][0]["observable_at_utc_ns"] -= 1
    elif mutation == "cross_run":
        raw["run_id"] = "foreign"
    elif mutation == "hash":
        raw["findings"][0]["evidence"][0]["sha256"] = "0" * 64
    elif mutation == "tier":
        raw["findings"][0]["evidence"][0]["tier"] = "M1"
    elif mutation == "no_finding_evidence":
        raw["findings"][0]["evidence"] = []
    elif mutation == "no_qc_evidence":
        raw["evidence"] = []
    else:
        raw["findings"] = []
        raw["service_violation"] = raw["observable_anomaly"] = False
    rows[0] = EventLabel.model_validate(raw)
    assert not audit_label_records(inputs, rows)["passed"]


def test_freshness_all_stations_maintenance_and_recovery(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path, gap=True)
    rows = build_labels(inputs)
    assert rows[0].service_violation is True and rows[1].service_violation is False
    assert len([f for f in rows[0].findings if f.kind == "reception_freshness"]) == 5
    assert audit_label_records(inputs, rows)["passed"]
    for row in inputs.master:
        if row["event_type"] == "master_business_started":
            plan = {
                **row,
                "event_id": "maintenance_plan",
                "event_type": "master_maintenance_plan",
                "station_id": "PV01",
                "window_start_utc_ns": 30 * 10**9,
                "window_end_utc_ns": 40 * 10**9,
                "recovery_deadline_s": 15,
            }
            break
    inputs.master.append(plan)
    path = "observed/master_events_master.jsonl"
    inputs.records[plan["event_id"]] = (path, plan)
    rows = build_labels(inputs)
    assert rows[0].service_violation is False
    assert audit_label_records(inputs, rows)["passed"]


def test_invalid_quality_is_never_a_normal_negative_example(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path)
    inputs.report["experiment_quality"]["passed"] = False
    rows = build_labels(inputs)
    assert all(r.label_status == "invalid" and r.service_violation is None for r in rows)


def test_applied_fault_without_business_violation_remains_negative(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path)
    fault = terminal_scenario("A05", StationScope(kind="station", station_ids=("PV01",)), 2)
    path = tmp_path / "truth/fault/effective.json"
    path.parent.mkdir()
    path.write_text(fault.model_dump_json(), encoding="utf-8")
    journal = path.with_name("events.jsonl")
    journal.write_text(
        "\n".join(
            json.dumps(dict(event_type=k, ts_event_utc_ns=t, after=dict(station_id="PV01")))
            for k, t in [("apply_verified", 20 * 10**9), ("restore_verified", 50 * 10**9)]
        ),
        encoding="utf-8",
    )
    inputs.hashes.update(
        {p.relative_to(tmp_path).as_posix(): file_sha256(p) for p in (path, journal)}
    )
    inputs.report["fault_verification"] = {"passed": True}
    rows = build_labels(inputs)
    assert rows[0].fault_applied is True and rows[0].service_violation is False
    assert rows[0].observable_anomaly is False
    assert audit_label_records(inputs, rows)["passed"]


@pytest.mark.parametrize(
    "path", ["../truth/x", "/etc/passwd", "C:/key", "truth/../observed/x", "truth\\x"]
)
def test_evidence_cannot_escape_run(path: str) -> None:
    with pytest.raises(ValueError):
        EvidenceRef(path=path, sha256="0" * 64, tier="Oracle", role="retrospective")


def test_future_and_oracle_evidence_cannot_be_online(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path, "timeout")
    raw = build_labels(inputs)[0].model_dump(mode="json")
    ref = raw["findings"][0]["evidence"][0]
    ref["available_at"] = 24 * 10**9
    with pytest.raises(ValueError, match="future evidence"):
        EventLabel.model_validate(raw)
    with pytest.raises(ValueError, match="online evidence"):
        EvidenceRef(
            path="truth/state.json", sha256="0" * 64, tier="Oracle", role="online", available_at=0
        )


@pytest.mark.parametrize("code", ["P03", "P04", "P06", "P03_recovery"])
def test_pairs_share_conditions_and_reject_physical_changes(code: str) -> None:
    pair = development_pair(code, "pair_unit_fixture")
    assert pair.normal.pair_id == pair.changed.background.pair_id
    assert pair.shared_conditions(pair.normal) == pair.shared_conditions(pair.changed.background)
    raw = pair.model_dump(mode="json")
    raw["changed"]["background"]["simulation"]["protocol"]["seed"] += 1
    with pytest.raises(ValueError, match="conditions differ"):
        PairConfig.model_validate(raw)


def test_p06_retains_device_operations_and_rejects_different_business_schedule() -> None:
    pair = development_pair("P06", "pair_unit")
    assert (
        pair.normal.simulation.device_operations
        == pair.changed.background.simulation.device_operations
    )
    assert pair.normal.simulation.device_operations[1].at_s == 40
    raw = pair.model_dump(mode="json")
    raw["changed"]["background"]["business_schedule"][0]["targets_kw"]["PV01"] = 21.0
    with pytest.raises(ValueError, match="conditions differ"):
        PairConfig.model_validate(raw)


@pytest.mark.parametrize("code", ["A01", "A06"])
def test_actual_fault_intervals_and_transport_have_independent_evidence(
    tmp_path: Path, code: str
) -> None:
    inputs = fixture_inputs(tmp_path)
    scope = StationScope(kind="station", station_ids=("PV01",))
    fault = a01_scenario(scope, 20) if code == "A01" else terminal_scenario(code, scope, 10)
    directory = tmp_path / "truth/fault"
    directory.mkdir()
    config_path, journal_path = directory / "effective.json", directory / "events.jsonl"
    config_path.write_text(fault.model_dump_json(), encoding="utf-8")
    journal_path.write_text(
        "\n".join(
            json.dumps(dict(event_type=k, ts_event_utc_ns=t * 10**9, after=dict(station_id="PV01")))
            for k, t in (("apply_verified", 20), ("restore_verified", 40))
        ),
        encoding="utf-8",
    )
    for path in (config_path, journal_path):
        inputs.hashes[path.relative_to(tmp_path).as_posix()] = file_sha256(path)
    inputs.report["fault_verification"] = {"passed": True}
    if code == "A06":
        hold = dict(
            event_id="hold",
            event_type="terminal_fault_feedback_hold",
            station_id="PV01",
            fault_event_id=fault.fault.event_id,
            ts_event_utc_ns=25 * 10**9,
        )
        inputs.terminal.append(hold)
        inputs.records["hold"] = ("truth/terminal_events_test.jsonl", hold)
        inputs.report["communication_effects"] = dict(
            stations=dict(PV01=dict(passed=True, begin_utc_ns=25 * 10**9, end_utc_ns=40 * 10**9))
        )
    else:
        inputs.report["communication_effects"] = dict(
            verified_blocked_interval_utc_ns=[20 * 10**9, 39 * 10**9],
            stations=dict(PV01=dict(withheld_evidence=[dict(p1_frame_ref=123)])),
        )
    rows = build_labels(inputs)
    assert rows[0].fault_start_utc_ns == (25 if code == "A06" else 20) * 10**9
    assert rows[0].communication_impact is True
    assert rows[0].transport_impact is (True if code == "A01" else None)
    assert audit_label_records(inputs, rows)["passed"]
    raw = rows[0].model_dump(mode="json")
    raw["fault_start_utc_ns"] -= 1
    rows[0] = EventLabel.model_validate(raw)
    assert not audit_label_records(inputs, rows)["passed"]


def test_full_recovery_requires_new_complete_gi_not_first_old_measurement(tmp_path: Path) -> None:
    from pvcomm.pipeline.labels import full_recovery

    inputs = fixture_inputs(tmp_path)
    request = dict(
        event_id="request",
        event_type="master_interrogation_requested",
        station_id="PV01",
        dispatch_id="recovery",
        available_at=60 * 10**9,
    )
    result = dict(
        event_id="result",
        event_type="master_interrogation_result",
        station_id="PV01",
        dispatch_id="recovery",
        available_at=61 * 10**9,
        success=True,
    )
    for event in (request, result):
        inputs.master.append(event)
        inputs.records[event["event_id"]] = ("observed/master_events_master.jsonl", event)
    assert full_recovery(inputs, "PV01", 40 * 10**9) == (None, [])

    for index, (type_id, cot, ioa) in enumerate(
        [
            (100, 6, 0),
            (100, 7, 0),
            *[(13, 20, n) for n in (1001, 1002, 1003, 1006, 1008)],
            (1, 20, 2001),
            (100, 10, 0),
        ]
    ):
        obj = dict(
            apdu_id=f"gi{index}",
            object_index=0,
            type_id=type_id,
            cot=cot,
            ioa=ioa,
            ca=1,
            flow_id=2,
            available_at=60 * 10**9 + index + 1,
            src_ip="10.204.0.1" if cot == 6 else "10.204.1.2",
            dst_ip="10.204.1.2" if cot == 6 else "10.204.0.1",
        )
        inputs.objects.append(obj)
        inputs.records[f"gi{index}:0"] = (f"parsed/{PARSER_VERSION}/iec104_objects.jsonl", obj)
    complete, refs = full_recovery(inputs, "PV01", 40 * 10**9)
    assert complete == 60 * 10**9 + 9 and refs
    # GI termination alone or partial measurement transfer cannot prove full recovery.
    inputs.objects[:] = [r for r in inputs.objects if r.get("ioa") != 1008]
    assert full_recovery(inputs, "PV01", 40 * 10**9) == (None, [])


@pytest.mark.parametrize("negative", (True, False))
def test_late_confirmation_is_not_power_execution(tmp_path: Path, negative: bool) -> None:
    inputs = fixture_inputs(tmp_path, "timeout")
    ack = {
        **inputs.objects[0],
        "apdu_id": "late",
        "cot": 7,
        "available_at": 25 * 10**9,
        "src_ip": "10.204.1.2",
        "negative_confirm": negative,
    }
    inputs.objects.append(ack)
    inputs.records["late:0"] = (f"parsed/{PARSER_VERSION}/iec104_objects.jsonl", ack)
    rows = build_labels(inputs)
    finding = rows[0].findings[0]
    assert finding.end_utc_ns == finding.recovery_utc_ns == 25 * 10**9
    assert finding.recovery_kind == (
        "late_negative_confirmation" if negative else "late_positive_confirmation"
    )
    assert finding.execution_recovery_utc_ns is None
    assert audit_label_records(inputs, rows)["passed"]
    raw = rows[0].model_dump(mode="json")
    raw["findings"][0]["recovery_kind"] = (
        "late_positive_confirmation" if negative else "late_negative_confirmation"
    )
    rows[0] = EventLabel.model_validate(raw)
    assert not audit_label_records(inputs, rows)["passed"]


def test_feedback_restoration_does_not_prove_execution(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path, "missing_feedback")
    point = dict(
        inputs.master[1],
        event_id="recovered_setpoint",
        ioa=1006,
        station_id="PV01",
        value=20.0,
        available_at=35 * 10**9,
        ts_mono_ns=35 * 10**9,
    )
    inputs.master.append(point)
    inputs.records[point["event_id"]] = ("observed/master_events_master.jsonl", point)
    rows = build_labels(inputs)
    finding = rows[0].findings[0]
    assert finding.recovery_kind == "feedback" and finding.recovery_utc_ns == 35 * 10**9
    assert finding.execution_recovery_utc_ns is None
    assert audit_label_records(inputs, rows)["passed"]
    raw = rows[0].model_dump(mode="json")
    raw["findings"][0]["execution_recovery_utc_ns"] = 35 * 10**9
    rows[0] = EventLabel.model_validate(raw)
    assert not audit_label_records(inputs, rows)["passed"]


def test_same_value_successor_cannot_restore_previous_transaction(tmp_path: Path) -> None:
    inputs = fixture_inputs(tmp_path, "timeout")
    original = next(r for r in inputs.master if r["event_type"] == "master_command_sent")
    successor = {
        **original,
        "event_id": "second",
        "task_id": "transaction_2",
        "available_at": 40 * 10**9,
        "ts_mono_ns": 40 * 10**9,
        "sent_mono_ns": 40 * 10**9,
        "sent_utc_ns": 40 * 10**9,
    }
    inputs.master.append(successor)
    inputs.records["second"] = ("observed/master_events_master.jsonl", successor)
    for cot in (6, 7):
        obj = {
            **inputs.objects[0],
            "apdu_id": f"second_{cot}",
            "cot": cot,
            "available_at": 40 * 10**9 + cot,
            "negative_confirm": False,
            "src_ip": "10.204.1.2" if cot == 7 else "10.204.0.1",
        }
        inputs.objects.append(obj)
        inputs.records[f"second_{cot}:0"] = (f"parsed/{PARSER_VERSION}/iec104_objects.jsonl", obj)
    config = normal_scenario("N04").simulation.protocol
    inputs.report["experiment_quality"]["command_assessments"]["PV01"] = audit_commands(
        inputs.master, inputs.terminal, inputs.objects, "PV01", 1, config.stations[0].address
    )
    rows = build_labels(inputs)
    finding = rows[0].findings[0]
    assert finding.end_utc_ns == finding.superseded_at_utc_ns == 40 * 10**9
    assert finding.recovery_censored and finding.recovery_utc_ns is None
    assert audit_label_records(inputs, rows)["passed"]


@pytest.mark.parametrize(
    "mutation",
    (
        "none",
        "normal_violation",
        "no_violation",
        "wrong_target",
        "control_violation",
        "invalid",
        "uncertain",
        "null",
    ),
)
def test_pair_qc_requires_station_specific_business_contrast(tmp_path: Path, mutation: str) -> None:
    from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
    from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION
    from pvcomm.qc.pairs import qc_pair

    config = development_pair("P03", "pair_unit_only")
    rows = {}
    for member in ("normal", "changed"):
        inputs = fixture_inputs(tmp_path / f"fixture_{member}", gap=member == "changed")
        rows[member] = [r.model_dump(mode="json") for r in build_labels(inputs)]
    if mutation == "normal_violation":
        rows["normal"][0] = dict(rows["changed"][0])
    elif mutation == "no_violation":
        rows["changed"][0] = dict(rows["normal"][0])
    elif mutation in ("wrong_target", "control_violation"):
        bad = json.loads(json.dumps(rows["changed"][0]))
        bad["station_id"] = "PV02"
        for finding in bad["findings"]:
            finding["station_id"] = "PV02"
        rows["changed"][1] = bad
        if mutation == "wrong_target":
            rows["changed"][0] = dict(rows["normal"][0])
    elif mutation in ("invalid", "uncertain", "null"):
        raw = rows["normal"][0]
        raw["label_status"] = "invalid" if mutation == "invalid" else "uncertain"
        raw["service_violation"] = raw["observable_anomaly"] = None
        raw["findings"][0]["service_violation"] = None
        raw["findings"][0]["label_status"] = "uncertain"
    directory = tmp_path / "pair"
    directory.mkdir()
    (directory / "pair.json").write_text(config.model_dump_json())
    ledger = []
    for member in ("normal", "changed"):
        run_id = f"run_{member}"
        root = directory / run_id
        for subdir in (
            "truth/scenario",
            "observed",
            f"parsed/{PARSER_VERSION}",
            f"labels/{LABEL_VERSION}",
            "qc/unit-quality",
            f"qc/{LABEL_QC_VERSION}",
        ):
            (root / subdir).mkdir(parents=True, exist_ok=True)
        scene = (config.normal if member == "normal" else config.changed.background).model_dump(
            mode="json"
        )
        scene["simulation"]["protocol"]["run_id"] = run_id
        (root / "truth/scenario/effective.json").write_text(json.dumps(scene))
        (root / "manifest.json").write_text(
            json.dumps(
                dict(
                    status="completed",
                    code_dirty=False,
                    cleanup_errors=[],
                    scene_clock=dict(start_mono_ns=0),
                    clock_origin=dict(ts_utc_ns=0, ts_mono_ns=0),
                    network=dict(addresses=[]),
                )
            )
        )
        (root / "observed/master_events_fixture.jsonl").write_text(
            json.dumps(dict(event_type="master_business_started", sim_elapsed_ms=0)) + "\n"
        )
        (root / f"parsed/{PARSER_VERSION}/packets.jsonl").write_text("")
        for report_name in ("unit-quality", LABEL_QC_VERSION):
            (root / f"qc/{report_name}/report.json").write_text('{"passed":true}')
        for row in rows[member]:
            row["run_id"] = run_id
        (root / f"labels/{LABEL_VERSION}/event_labels.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows[member])
        )
        (root / f"qc/{LABEL_QC_VERSION}/report.json").write_text(
            json.dumps(
                dict(
                    passed=True,
                    label_sha256=file_sha256(root / f"labels/{LABEL_VERSION}/event_labels.jsonl"),
                )
            )
        )
        ledger.append(dict(member=member, run_id=run_id, run_dir=run_id))
    (directory / "pair-manifest.json").write_text(
        json.dumps(dict(members=ledger, shared_sha256=config.shared_sha256()))
    )
    report = qc_pair(directory)
    assert report["data_quality_passed"]  # These synthetic counterexamples have intact files.
    assert report["passed"] is (mutation == "none")
    assert report["contrast_observed"] is (mutation == "none")
