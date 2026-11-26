"""Synthetic unit fixtures only: these events are not simulation evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.pairs import PairConfig, development_pair
from pvcomm.domain.labels import EventLabel, EvidenceRef
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.pipeline.labels import build_labels, load_label_inputs
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


@pytest.mark.parametrize("mutation", ["deadline", "cross_run", "hash", "missing", "tier"])
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
