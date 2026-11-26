"""Synthetic scheduler/contract fixtures; these do not establish lab coverage."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from pvcomm.business.schedule import execute_schedule
from pvcomm.cli import main
from pvcomm.config.normal_scenes import SCENE_CODES, normal_scenario
from pvcomm.config.presets import configuration
from pvcomm.config.scenes import ScenarioConfig, canonical_hash, instantiate_scenario, load_scenario
from pvcomm.domain.command import CommandResult, ObservationUnavailable
from pvcomm.protocol.c104_runtime import C104Master
from pvcomm.qc.schedule import audit_schedule
from pvcomm.web.observation_index import ObservationIndex


def document(tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    simulation = configuration(3, 240)
    simulation.update(dispatch_tasks=[], repeat_command_after_s=None)
    return {
        "schema_version": "0.4-scenario.1",
        "purpose": "normal_scenario_development",
        "source_group_id": "source_fixture",
        "normal_scene": "N01",
        "simulation": simulation,
        "business_schedule": tasks or [],
    }


def setpoint(*, station: str = "PV01", at: int = 20, identity: str = "task_1") -> dict[str, Any]:
    return dict(
        kind="setpoint",
        schedule_id=identity,
        at_s=at,
        station_ids=[station],
        targets_kw={station: 10},
    )


class RecordingMaster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def available_power(self, station_config: Any) -> float:
        if station_config.station_id == "PV02":
            raise ObservationUnavailable("PV02", "stale")
        return 12.0

    def send_setpoint(
        self,
        station_config: Any,
        target_kw: float,
        action: str,
        *,
        dispatch_id: str | None = None,
        raise_on_failure: bool = True,
    ) -> CommandResult:
        assert raise_on_failure is False
        station = station_config.station_id
        self.calls.append((station, "setpoint", dispatch_id))
        return CommandResult("unit_" + station, station, "accepted", True, True, 1)

    def interrogate(
        self, station_config: Any, *, dispatch_id: str, raise_on_failure: bool = True
    ) -> bool:
        assert raise_on_failure is False
        self.calls.append((station_config.station_id, "interrogation", dispatch_id))
        return True


def test_explicit_empty_schedule_never_issues_legacy_limits() -> None:
    scenario = ScenarioConfig.model_validate(document())
    master = RecordingMaster()
    events = []
    execute_schedule(scenario, master, lambda _: None, lambda *e: events.append(e))
    assert not master.calls and not events
    assert scenario.simulation.protocol.command_after_s == 10  # Legacy fields remain intact.
    assert audit_schedule(scenario, [], [], [], {}, [])["passed"]
    assert not audit_schedule(
        scenario,
        [{"event_type": "master_command_sent", "station_id": "PV01", "dispatch_id": None}],
        [],
        [],
        {},
        [],
    )["passed"]


def test_single_station_and_gi_issue_only_due_intents() -> None:
    raw = document(
        [
            setpoint(),
            dict(kind="interrogation", schedule_id="task_2", at_s=90, station_ids=["PV02"]),
        ]
    )
    scenario = ScenarioConfig.model_validate(raw)
    master = RecordingMaster()
    due = 0.0
    lock = threading.Lock()
    events = []

    def wait_until(at: float) -> None:
        nonlocal due
        due = at

    def record(kind: str, payload: dict[str, object]) -> None:
        with lock:
            if kind == "master_schedule_started":
                assert due >= float(str(payload["scheduled_at_s"]))
            assert not {"normal_scene", "source_group_id", "pair_id"} & set(payload)
            events.append((kind, payload))

    execute_schedule(scenario, master, wait_until, record)
    assert set(master.calls) == {
        ("PV01", "setpoint", "task_1"),
        ("PV02", "interrogation", "task_2"),
    }


def test_dispatch_subset_reuses_missing_observation_policy_and_continues() -> None:
    tasks = [
        dict(
            kind="dispatch",
            schedule_id="task_1",
            at_s=20,
            station_ids=["PV01", "PV02"],
            allocation="observed_available",
            total_kw=50,
            batch_size=1,
        ),
        setpoint(at=120, identity="task_2"),
    ]
    master = RecordingMaster()
    events: list[tuple[str, dict[str, object]]] = []
    execute_schedule(
        ScenarioConfig.model_validate(document(tasks)),
        master,
        lambda _: None,
        lambda k, p: events.append((k, p)),
    )
    assert set(master.calls) == {("PV01", "setpoint", "task_1"), ("PV01", "setpoint", "task_2")}
    allocation = next(p for k, p in events if k == "master_dispatch_allocated")
    assert allocation["excluded_observations"] == {"PV02": "stale"}
    assert allocation["unallocated_kw"] == 38


def test_seeded_plan_is_reproducible_and_independent_of_runtime_identity() -> None:
    task = {**setpoint(), "jitter_s": 30}
    scenario = ScenarioConfig.model_validate(document([task]))
    plans = []
    for seed in range(10):
        raw = scenario.model_dump(mode="json")
        raw["simulation"]["protocol"]["seed"] = seed
        variant = ScenarioConfig.model_validate(raw)
        plan = variant.expanded_schedule()
        assert plan == variant.expanded_schedule()
        assert plan == instantiate_scenario(variant, "run_other").expanded_schedule()
        assert 20 <= plan[0].at_s <= 50
        plans.append(canonical_hash([t.model_dump(mode="json") for t in plan]))
    assert len(set(plans)) > 1


@pytest.mark.parametrize(
    "failure", ["unknown", "duplicate", "overlap", "capacity", "end", "legacy"]
)
def test_invalid_schedule_fails_closed(failure: str) -> None:
    raw = document([setpoint()])
    task = raw["business_schedule"][0]
    if failure == "unknown":
        task["station_ids"] = ["PV99"]
    elif failure == "duplicate":
        task["station_ids"] = ["PV01", "PV01"]
    elif failure == "overlap":
        raw["business_schedule"].append(setpoint(at=60, identity="task_2"))
    elif failure == "capacity":
        task["targets_kw"] = {"PV01": 31}
    elif failure == "end":
        task["at_s"] = 181
    else:
        raw["simulation"]["repeat_command_after_s"] = 35
    with pytest.raises(ValueError):
        ScenarioConfig.model_validate(raw)


def test_schedule_required_and_yaml_duplicate_keys_rejected(tmp_path: Path) -> None:
    raw = document()
    del raw["business_schedule"]
    with pytest.raises(ValueError):
        ScenarioConfig.model_validate(raw)
    source = tmp_path / "scene.yaml"
    source.write_text("schema_version: x\nschema_version: y", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_scenario(source)


def test_cli_expansion_and_invalid_identity(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "scenario.yaml"
    path.write_text(json.dumps(document()), encoding="utf-8")
    assert main(["validate-scenario", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["expanded_plan"] == []
    assert (
        main(
            [
                "run-scenario",
                "--config",
                str(path),
                "--output-root",
                str(tmp_path),
                "--run-id",
                "../escape",
            ]
        )
        == 1
    )
    assert not (tmp_path / "escape").exists()


def test_m2_schedule_intent_is_causal_and_metadata_is_not_projected(tmp_path: Path) -> None:
    path = tmp_path / "observed.jsonl"
    path.write_text(
        json.dumps(
            dict(
                event_type="master_schedule_started",
                event_id="observed_1",
                dispatch_id="task_1",
                available_at=100,
                normal_scene="N09",
                pair_id="pair_fixture",
                source_group_id="source_fixture",
                business_schedule=["future task"],
                truth={"injected": True},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    index = ObservationIndex(tmp_path / "index.sqlite")
    try:
        index.ingest(path)
        assert not index.rows(99)
        row = index.rows(100)[0]
        assert row["dispatch_id"] == "task_1"
        assert not {
            "normal_scene",
            "pair_id",
            "source_group_id",
            "business_schedule",
            "truth",
        } & set(row)
    finally:
        index.connection.close()


def test_normal_case_templates_are_explicit_and_differentiated() -> None:
    for code in SCENE_CODES:
        variants = [normal_scenario(code, v) for v in (0, 1, 2)]
        assert len({canonical_hash(v.model_dump(mode="json")) for v in variants}) == 3
        assert all(not v.simulation.dispatch_tasks for v in variants)
        assert all(v.simulation.repeat_command_after_s is None for v in variants)
        if code in ("N01", "N02", "N03", "N10"):
            assert all(not v.expanded_schedule() for v in variants)


def test_normal_audit_requires_proven_transaction_and_legal_rejection_state() -> None:
    scene = ScenarioConfig.model_validate(document([setpoint()]))
    master: list[dict[str, Any]] = [
        dict(
            event_type="master_schedule_started",
            event_id="s",
            dispatch_id="task_1",
            kind="setpoint",
            station_ids=["PV01"],
            sim_elapsed_ms=20_000,
            ts_mono_ns=100,
        ),
        dict(
            event_type="master_command_sent",
            event_id="c",
            dispatch_id="task_1",
            station_id="PV01",
            target_kw=10,
            ts_mono_ns=101,
        ),
        dict(
            event_type="master_schedule_finished",
            event_id="f",
            dispatch_id="task_1",
            ts_mono_ns=105,
        ),
    ]
    wire = [dict(type_id=50, cot=6)]
    assessments = {
        "PV01": [
            dict(command_event="c", transaction_evidence_valid=True, outcome="accepted_complete")
        ]
    }
    assert audit_schedule(scene, master, [], wire, assessments, [])["passed"]
    assert not audit_schedule(scene, master, [], wire, {}, [])["passed"]
    assessments["PV01"][0].update(outcome="rejected", rejection_event="r")
    assert not audit_schedule(scene, master, [], wire, assessments, [])["passed"]
    rejection = [dict(event_id="r", accepted_setpoint_kw=30)]
    states = [
        dict(
            station_id="PV01",
            model_mono_ns=100,
            p_set_accepted_kw=30,
            enabled=False,
            connected=True,
            faulted=False,
        )
    ]
    assert audit_schedule(scene, master, rejection, wire, assessments, states)["passed"]
    states[0]["enabled"] = True
    assert not audit_schedule(scene, master, rejection, wire, assessments, states)["passed"]
    assert not audit_schedule(scene, master, [], wire * 2, assessments, [])["passed"]


def test_gi_audit_never_reuses_startup_or_pre_request_data() -> None:
    task = dict(kind="interrogation", schedule_id="task_1", at_s=20, station_ids=["PV01"])
    scene = ScenarioConfig.model_validate(document([task]))
    master: list[dict[str, Any]] = [
        dict(
            event_type="master_schedule_started",
            event_id="s",
            dispatch_id="task_1",
            kind="interrogation",
            station_ids=["PV01"],
            sim_elapsed_ms=20_000,
            ts_mono_ns=100,
        ),
        dict(
            event_type="master_interrogation_requested",
            event_id="q",
            dispatch_id="task_1",
            station_id="PV01",
            available_at=100,
        ),
        dict(
            event_type="master_interrogation_result",
            event_id="a",
            dispatch_id="task_1",
            station_id="PV01",
            available_at=106,
            success=True,
        ),
        dict(
            event_type="master_schedule_finished",
            event_id="f",
            dispatch_id="task_1",
            ts_mono_ns=110,
        ),
    ]
    wire: list[dict[str, Any]] = [
        dict(
            ca=1,
            type_id=kind,
            cot=cot,
            available_at=stamp,
            ioa=ioa,
            flow_id="flow_1",
            src_ip="10.204.1.2" if cot != 6 else "master",
            dst_ip="10.204.1.2",
            apdu_id=str(stamp),
        )
        for kind, cot, stamp, ioa in (
            (100, 6, 101, 0),
            (100, 7, 102, 0),
            (13, 20, 103, 1001),
            (13, 20, 104, 1006),
            (100, 10, 105, 0),
        )
    ]
    assert audit_schedule(scene, master, [], wire, {}, [])["passed"]
    for row in wire:
        row["available_at"] -= 100
    assert not audit_schedule(scene, master, [], wire, {}, [])["passed"]


def test_allocator_cannot_consume_observation_before_evidence_is_published() -> None:
    master = object.__new__(C104Master)
    master._observation_lock = threading.Lock()
    master._observations = {}
    master._invalid_observations = set()
    publishing = threading.Event()
    release = threading.Event()
    committed: list[dict[str, object]] = []

    def record(kind: str, payload: dict[str, object]) -> None:
        publishing.set()
        assert release.wait(2)
        committed.append(payload)

    master._record_event = record
    writer = threading.Thread(
        target=master._publish_observation, args=("PV01", 1008, 12.0, True, "PERIODIC")
    )
    writer.start()
    try:
        assert publishing.wait(2)
        assert not master._observations
        assert not master._observation_lock.acquire(blocking=False)
    finally:
        release.set()
        writer.join(2)
    assert not writer.is_alive()
    assert (
        committed
        and master.available_power(normal_scenario("N01").simulation.protocol.stations[0]) == 12
    )


def test_failed_observation_publication_does_not_publish_an_unrecorded_value() -> None:
    master = object.__new__(C104Master)
    master._observation_lock = threading.Lock()
    master._observations = {}
    master._invalid_observations = set()

    def fail(kind: str, payload: dict[str, object]) -> None:
        raise OSError("synthetic full disk fixture")

    master._record_event = fail
    with pytest.raises(OSError):
        master._publish_observation("PV01", 1008, 12.0, True, "PERIODIC")
    assert not master._observations
