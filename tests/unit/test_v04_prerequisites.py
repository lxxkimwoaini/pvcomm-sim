"""Synthetic unit fixtures only; real fault evidence is exercised separately in lab."""

import gzip
import json
import struct
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pvcomm.business.dispatch import execute_dispatches
from pvcomm.config.multisite import V03Config
from pvcomm.config.presets import configuration
from pvcomm.domain.command import CommandOutcome, CommandResult, ObservationUnavailable
from pvcomm.protocol.c104_runtime import C104Master
from pvcomm.protocol.confirmation import Confirmation, ConfirmationTracker, confirmations
from pvcomm.qc.commands import audit_commands
from pvcomm.qc.experiment import (
    PROTOCOL_INTEGRITY,
    PROTOCOL_QUALITY,
    classify_quality,
)
from pvcomm.storage.retention import RetentionPolicy, archive_lossless, storage_budget


def frame(*, negative: bool = False, test: bool = False, target: float = 20) -> bytes:
    return (
        bytes.fromhex("6812000000003201")
        + bytes(
            [
                7 | (0x40 if negative else 0) | (0x80 if test else 0),
                0,
                1,
                0,
            ]
        )
        + (3001).to_bytes(3, "little")
        + struct.pack("<f", target)
        + b"\x00"
    )


def test_raw_confirmation_decoder_does_not_infer_negative_from_transmit_boolean() -> None:
    positive, negative = confirmations(frame() + frame(negative=True))
    assert positive.positive and not negative.positive
    assert positive.ioa == 3001 and positive.common_address == 1
    assert confirmations(frame(test=True)) == []
    assert confirmations(frame(target=float("nan"))) == []
    with pytest.raises(ValueError, match="incomplete"):
        confirmations(frame()[:-1])


def test_tracker_timeout_quarantine_late_same_value_and_new_session() -> None:
    tracker = ConfirmationTracker()
    tracker.state("PV01", "OPEN")
    now = time.monotonic_ns()
    assert tracker.begin("PV01", "first", 1, 20, now - 4_000_000_000, now - 1) is None
    assert tracker.observe("PV01", Confirmation(1, 3001, 20, True, "late"), now) is None
    assert tracker.finish("PV01") == ("confirmation_timeout", None)
    assert tracker.begin("PV01", "second", 1, 20, now, now + 1_000_000) == "session_quarantined"
    tracker.state("PV01", "OPEN_MUTED")
    tracker.state("PV01", "OPEN")
    assert "PV01" in tracker.quarantined  # STOPDT/STARTDT is not a fresh TCP session.
    tracker.state("PV01", "CLOSED")
    tracker.state("PV01", "OPEN")
    assert tracker.epochs["PV01"] == 2
    assert tracker.begin("PV01", "second", 1, 20, now, now + 1_000_000_000) is None
    assert (
        tracker.observe("PV01", Confirmation(1, 3001, 20, False, "real-negative"), now) == "second"
    )
    assert tracker.finish("PV01") == ("negative_confirmation", False)


def test_tracker_exclusive_deadline_wrong_station_ca_value_and_duplicates() -> None:
    tracker = ConfirmationTracker()
    tracker.state("PV01", "OPEN")
    now = time.monotonic_ns()
    assert tracker.begin("PV01", "one", 1, 20, now, now + 1_000_000) is None
    for station, ca, value, timestamp in (
        ("PV02", 1, 20, now),
        ("PV01", 2, 20, now),
        ("PV01", 1, 21, now),
        ("PV01", 1, 20, now + 1_000_000),
    ):
        assert (
            tracker.observe(station, Confirmation(ca, 3001, value, True, "unit"), timestamp) is None
        )
    for _ in range(2):
        assert tracker.observe("PV01", Confirmation(1, 3001, 20, True, "unit"), now) == "one"
    assert tracker.finish("PV01") == ("ambiguous_confirmation", None)


def test_tracker_connection_loss_is_not_an_early_confirmation_timeout() -> None:
    tracker = ConfirmationTracker()
    now = time.monotonic_ns()
    assert tracker.begin("PV01", "one", 1, 20, now, now + 3_000_000_000) == "connection_unavailable"
    tracker.state("PV01", "OPEN")
    tracker.begin("PV01", "one", 1, 20, now, now + 3_000_000_000)
    tracker.state("PV01", "CLOSED")
    assert tracker.finish("PV01") == ("connection_lost", None)


@pytest.mark.parametrize("condition", ["missing", "stale", "invalid", "nan", "future"])
def test_availability_missing_stale_and_invalid_are_never_zero(condition: str) -> None:
    master = C104Master.__new__(C104Master)
    master._observation_lock = threading.Lock()
    master._observations = {}
    master._invalid_observations = set()
    station = V03Config.model_validate(configuration(3, 120)).protocol.stations[0]
    if condition != "missing":
        master._observations[(station.station_id, 1008)] = (
            float("nan") if condition == "nan" else 0.0,
            time.monotonic()
            + (10 if condition == "future" else -100 if condition == "stale" else 0),
        )
    if condition == "invalid":
        master._invalid_observations.add((station.station_id, 1008))
    with pytest.raises(ObservationUnavailable) as raised:
        master.available_power(station)
    assert raised.value.reason == {"nan": "invalid", "future": "stale"}.get(condition, condition)


def test_dispatch_skips_missing_station_continues_rejected_station_and_later_tasks() -> None:
    raw = configuration(3, 240)
    raw["dispatch_tasks"] = [
        dict(
            dispatch_id=f"d{i}",
            at_s=at,
            region_ids=["R1", "R2", "R3"],
            total_kw=30,
            allocation="observed_available",
            batch_size=3,
        )
        for i, at in enumerate((15, 100))
    ]
    config = V03Config.model_validate(raw)
    records: list[dict[str, Any]] = []
    sent: list[str] = []

    class TestMaster:
        def available_power(self, station: Any) -> float:
            if station.station_id == "PV01":
                raise ObservationUnavailable(station.station_id, "missing")
            return 10.0

        def send_setpoint(self, station: Any, *args: Any, **kwargs: Any) -> CommandResult:
            assert kwargs["raise_on_failure"] is False
            sent.append(station.station_id)
            outcome: CommandOutcome = (
                "negative_confirmation" if station.station_id == "PV02" else "accepted"
            )
            return CommandResult(
                "unit", station.station_id, outcome, outcome == "accepted", False, 1
            )

    execute_dispatches(
        config,
        TestMaster(),
        lambda _: None,
        lambda kind, payload: records.append({"event_type": kind, **payload}),
    )
    assert sorted(sent) == ["PV02", "PV02", "PV03", "PV03"]
    allocations = [r for r in records if r["event_type"] == "master_dispatch_allocated"]
    assert len(allocations) == 2 and all(r["unallocated_kw"] == 10 for r in allocations)
    assert all(r["excluded_observations"] == {"PV01": "missing"} for r in allocations)


def quality_fixture() -> dict[str, Any]:
    return {
        "run_id": "synthetic-quality-unit",
        "run_code_dirty": False,
        "passed": False,
        "checks": {
            **{k: {"passed": True} for k in PROTOCOL_INTEGRITY | PROTOCOL_QUALITY},
            "three_station_business_loop": {
                "passed": False,
                "evidence": {"PV01": {"commands": [{"outcome": "confirmation_timeout"}]}},
            },
            "dual_endpoint_correspondence": {
                "passed": False,
                "evidence": {
                    "PV01": {
                        "same_bytes": False,
                        "terminal_callback_match": True,
                        "incomplete_bytes": 0,
                    },
                },
            },
        },
    }


@pytest.mark.parametrize("programming_error", [False, True])
def test_all_observations_missing_is_explicit_but_programming_errors_propagate(
    programming_error: bool,
) -> None:
    raw = configuration(3, 240)
    raw["dispatch_tasks"] = [
        dict(
            dispatch_id="all_missing",
            at_s=20,
            region_ids=["R1", "R2", "R3"],
            total_kw=100,
            allocation="observed_available",
            batch_size=3,
        )
    ]
    records: list[dict[str, Any]] = []

    class Master:
        def available_power(self, station: Any) -> float:
            if programming_error:
                raise RuntimeError("unit programming defect")
            raise ObservationUnavailable(station.station_id, "missing")

        def send_setpoint(self, *args: Any, **kwargs: Any) -> CommandResult:
            raise AssertionError("no station has usable observations")

    def execute() -> None:
        execute_dispatches(
            V03Config.model_validate(raw),
            Master(),
            lambda _: None,
            lambda kind, payload: records.append({"event_type": kind, **payload}),
        )

    if programming_error:
        with pytest.raises(RuntimeError, match="programming defect"):
            execute()
    else:
        execute()
        assert records[0]["eligible_station_count"] == 0
        assert records[0]["allocated_kw"] == 0
        assert records[0]["unallocated_kw"] == raw["dispatch_tasks"][0]["total_kw"]
        assert records[0]["excluded_observations"]


def test_quality_is_independent_of_business_and_transport_success() -> None:
    report = classify_quality(quality_fixture())
    assert report["passed"] and not report["normal_regression"]["passed"]
    assert report["fault_verification"]["status"] == "not_evaluated"
    assert report["label_quality"]["status"] == "not_evaluated"
    assert report["transport_observations"]["PV01"]["same_bytes"] is False


@pytest.mark.parametrize("defect", ["drop", "unknown", "missing", "callback", "dirty"])
def test_invalid_experiment_cannot_pass_quality_under_expected_business_failure(
    defect: str,
) -> None:
    protocol = quality_fixture()
    if defect == "drop":
        protocol["checks"]["zero_capture_drops"]["passed"] = False
    elif defect == "unknown":
        protocol["checks"]["unclassified_new_check"] = {"passed": True}
    elif defect == "missing":
        del protocol["checks"]["raw_integrity"]
    elif defect == "callback":
        protocol["checks"]["dual_endpoint_correspondence"]["evidence"]["PV01"][
            "terminal_callback_match"
        ] = False
    else:
        protocol["run_code_dirty"] = True
    assert not classify_quality(protocol)["passed"]


def test_no_timeout_or_feedback_label_before_actual_deadline() -> None:
    command = dict(
        event_id="send",
        event_type="master_command_sent",
        station_id="PV01",
        task_id="unit",
        ioa=3001,
        target_kw=20,
        ts_mono_ns=0,
        ts_event_utc_ns=0,
        deadline_ms=3000,
    )
    wire = dict(
        apdu_id="p0", ca=1, ioa=3001, type_id=50, cot=6, dst_ip="rtu", value_num=20, available_at=1
    )
    master = [
        command,
        dict(event_type="master_business_finished", station_id="PV01", ts_mono_ns=2_999_999_999),
    ]
    assert (
        audit_commands(master, [], [wire], "PV01", 1, "rtu")[0]["outcome"]
        == "incomplete_or_ambiguous"
    )
    master[-1]["ts_mono_ns"] = 3_000_000_000
    result = audit_commands(master, [], [wire], "PV01", 1, "rtu")[0]
    assert result["outcome"] == "confirmation_timeout" and result["transaction_evidence_valid"]


def test_retention_budget_and_lossless_archive_leave_source_and_existing_output(
    tmp_path: Path,
) -> None:
    policy = RetentionPolicy.model_validate_json(
        Path("configs/retention-development.json").read_bytes()
    )
    budget = storage_budget(policy)
    assert budget["retained_capacity_bytes"] == 1_493_910_203_040
    assert not budget["temporary_peak_measured"] and not budget["concurrency_throughput_validated"]
    with pytest.raises(ValueError):
        RetentionPolicy.model_validate({**policy.model_dump(), "delete_sources": True})
    source = tmp_path / "unit-evidence.jsonl"
    content = b'{"fixture_only":true}\n' * 100
    source.write_bytes(content)
    destination = tmp_path / "new-archive"
    record = archive_lossless(source, destination)
    assert source.read_bytes() == content
    assert gzip.decompress((destination / "payload.gz").read_bytes()) == content
    assert record["restored_sha256"] == record["source_sha256"]
    assert json.loads((destination / "manifest.json").read_text())["source_retained"]
    with pytest.raises(FileExistsError):
        archive_lossless(source, destination)


def test_nonfinite_received_value_is_preserved_as_unknown_without_breaking_event_storage() -> None:
    from types import ModuleType, SimpleNamespace

    master = C104Master.__new__(C104Master)
    master._observations = {}
    master._invalid_observations = set()
    master._observation_lock = threading.Lock()
    callbacks: dict[int, Any] = {}
    points = {
        ioa: SimpleNamespace(
            io_address=ioa,
            value=float("nan"),
            info=SimpleNamespace(quality=SimpleNamespace(is_good=lambda: True)),
            on_receive=lambda callback, ioa=ioa: callbacks.update({ioa: callback}),
        )
        for ioa in (1001, 1002, 1003, 1004, 1005, 1006, 1008, 1009, 1010, 1011, 2001, 3001)
    }
    master._c104 = ModuleType("synthetic_unit_c104")
    master._c104.__dict__.update(
        Point=object,
        Information=object,
        IncomingMessage=object,
        ResponseState=SimpleNamespace(SUCCESS=True),
        Type=SimpleNamespace(M_ME_NC_1=13, M_SP_NA_1=1),
    )
    events: list[dict[str, Any]] = []

    def record(kind: str, payload: dict[str, object]) -> None:
        json.dumps(payload, allow_nan=False)
        events.append(payload)

    master._record_event = record
    station = V03Config.model_validate(configuration(3, 120)).protocol.stations[0]
    master._install_point_callbacks(
        station, SimpleNamespace(get_point=lambda io_address: points[io_address])
    )
    callbacks[1008](points[1008], None, SimpleNamespace(cot=SimpleNamespace(name="PERIODIC")))
    assert events[0]["value"] is None and events[0]["quality_valid"] is False
    with pytest.raises(ObservationUnavailable, match="invalid"):
        master.available_power(station)
