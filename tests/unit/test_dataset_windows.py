"""Explicit synthetic unit fixtures; never part of the real run catalogue."""

from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.config.power import power_document
from pvcomm.domain.labels import ServiceFinding
from pvcomm.pipeline.dataset import grouping_keys, split_records
from pvcomm.pipeline.features import (
    FEATURES,
    REQUIRED_IOAS,
    Observations,
    business_features,
    traffic_features,
    window_label,
)
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.scenario.v01_runner import V01Runner


def finding(start: int, end: int | None, **changes: Any) -> ServiceFinding:
    return ServiceFinding.model_validate(
        dict(
            finding_id="synthetic_unit_event",
            kind="reception_freshness",
            station_id="PV01",
            outcome="synthetic_unit_fixture",
            service_violation=True,
            label_status="valid",
            start_utc_ns=start,
            end_utc_ns=end,
            observable_at_utc_ns=start,
            recovery_utc_ns=end,
            recovery_censored=end is None,
            evidence=[],
        )
        | changes
    )


def test_historical_large_config_readable_but_execution_refused(tmp_path: Path) -> None:
    source = Path("configs/v0.3-30-short.yaml")
    config = power_document(source).protocol
    assert len(config.stations) == 30
    with pytest.raises(ValueError, match="at most 10"):
        run_isolated(config, source, tmp_path / "isolated")
    with pytest.raises(ValueError, match="at most 10"):
        V01Runner(config, source, tmp_path / "local").run()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("start,end,expected", [(0, 100, 0), (100, 110, 1), (110, 120, 0)])
def test_label_is_not_broadcast_to_whole_run(start: int, end: int, expected: int) -> None:
    assert window_label([finding(100, 110)], start, end)["label_at_end"] == expected


def test_window_union_censor_and_unknown() -> None:
    actual = window_label([finding(100, 130), finding(120, 140)], 90, 150)
    assert actual["anomaly_overlap_ratio"] == pytest.approx(40 / 60)
    assert actual["label_any"] == 1 and actual["label_at_end"] == 0
    assert window_label([finding(100, None)], 90, 150)["label_at_end"] == 1
    uncertain = finding(100, None, service_violation=None, label_status="uncertain")
    assert window_label([uncertain], 90, 150)["label_at_end"] is None
    superseded = finding(
        100, 130, recovery_utc_ns=None, recovery_censored=True, superseded_at_utc_ns=130
    )
    assert window_label([superseded], 130, 150)["label_any"] == 0


def test_deadline_at_cursor_and_recovery_boundary() -> None:
    assert window_label([finding(100, 110)], 40, 100)["label_any"] == 0
    assert window_label([finding(100, 110)], 50, 110)["label_at_end"] == 1
    assert window_label([finding(100, 110)], 51, 111)["label_at_end"] == 0


def test_service_violation_and_online_determination_remain_distinct() -> None:
    event = finding(100, 110, observable_at_utc_ns=105)
    label = window_label([event], 90, 103)
    assert label["label_at_end"] == 1
    assert label["observable_at_end"] == 0


def test_group_union_is_stable_and_precedes_windowing() -> None:
    rows: list[dict[str, Any]] = [
        dict(run_id=f"r{i}", path=str(i), group_keys=[f"day:{i}"]) for i in range(8)
    ]
    rows[1]["group_keys"] += ["pair:p"]
    rows[2]["group_keys"] += ["pair:p", "source:s"]
    rows[3]["group_keys"] += ["source:s"]
    assignments = split_records(rows, 17)
    assert assignments == split_records(list(reversed(rows)), 17)
    merged = [r for r in assignments if r["run_id"] in ("r1", "r2", "r3")]
    assert len({r["partition"] for r in merged}) == 1
    assert len({r["group_id"] for r in merged}) == 1
    assert {r["partition"] for r in assignments} == {"train", "validation", "test"}


def test_insufficient_sources_fail() -> None:
    with pytest.raises(ValueError, match="three independent"):
        split_records([dict(run_id="r1", path="1", group_keys=["same_date"])], 17)


def test_weather_dates_include_midnight_and_pair_groups() -> None:
    config = dict(
        protocol=dict(duration_s=120),
        weather=dict(
            source_start="2024-06-23T23:59:30Z",
            sha256="a" * 64,
        ),
    )
    keys = grouping_keys(config, dict(source_group_id="shared", pair_id="pair1"))
    assert any(k.endswith("2024-06-23") for k in keys)
    assert any(k.endswith("2024-06-24") for k in keys)
    assert "pair:pair1" in keys


def test_future_and_equal_cursor_observations_excluded(tmp_path: Path) -> None:
    db = Observations(tmp_path / "unit.sqlite")
    try:
        row = dict(
            event_type="master_observed_point",
            ioa=1001,
            value=5.0,
            available_at=10,
            quality_valid=True,
        )
        db.insert("master", "PV01", 10, row)
        db.insert("master", "PV01", 20, dict(row, value=999.0, available_at=20))
        assert db.latest_point("PV01", 1001, 20) == row
        assert db.latest_available(20) == 10
        assert db.latest_point("PV01", 1001, 10) is None
    finally:
        db.close()


def test_weather_interval_ending_at_midnight_excludes_next_day() -> None:
    keys = grouping_keys(
        dict(
            protocol=dict(duration_s=60),
            weather=dict(source_start="2024-06-23T23:59:00Z", sha256="a" * 64),
        ),
        {},
    )
    assert keys == [f"weather:{'a' * 64}:2024-06-23"]


def test_zero_traffic_is_a_sample_and_unknown_is_not_zero(tmp_path: Path) -> None:
    db = Observations(tmp_path / "unit.sqlite")
    try:
        traffic = traffic_features(db, "PV01", 0, 60 * 10**9)
        assert traffic["up_packets"] == traffic["down_packets"] == 0
        assert traffic["iat_mean_s"] is None
        business, _ = business_features(db, dict(station_id="PV01", telemetry_period_s=2), 10**9)
        assert business["power_kw"] is None and business["power_age_s"] is None
        assert business["fresh_point_coverage"] == 0
    finally:
        db.close()


def test_legal_negative_confirmation_is_not_timeout(tmp_path: Path) -> None:
    db = Observations(tmp_path / "unit.sqlite")
    try:
        for ioa in REQUIRED_IOAS:
            db.insert(
                "master",
                "PV01",
                8 * 10**9,
                dict(
                    event_type="master_observed_point",
                    ioa=ioa,
                    value=0.0,
                    available_at=8 * 10**9,
                    quality_valid=True,
                ),
            )
        db.insert(
            "master",
            "PV01",
            10**9,
            dict(
                event_type="master_command_sent",
                task_id="unit_task",
                sent_utc_ns=10**9,
                target_kw=10.0,
                deadline_ms=3000,
                execution_deadline_ms=10000,
                connection_epoch=1,
            ),
        )
        db.insert(
            "master",
            "PV01",
            2 * 10**9,
            dict(
                event_type="master_command_confirmation",
                task_id="unit_task",
                matched=True,
                positive_confirmation=False,
                connection_epoch=1,
            ),
        )
        feature, violation = business_features(
            db,
            dict(
                station_id="PV01",
                telemetry_period_s=2,
                capacity_kw=30.0,
            ),
            9 * 10**9,
        )
        assert not violation and feature["confirmation_wait_s"] == 0
    finally:
        db.close()


def test_whitelist_excludes_labels_ids_and_final_rule() -> None:
    forbidden = {
        "run_id",
        "station_id",
        "normal_scene",
        "fault_type",
        "business_rule",
        "service_violation",
        "finding_ids",
        "partition",
        "source_group_id",
    }
    assert not forbidden.intersection(FEATURES["M2"])
    assert set(FEATURES["M0"]) < set(FEATURES["M1"]) < set(FEATURES["M2"])
    with pytest.raises(ValueError):
        DatasetPolicy.model_validate(dict(window_s=30))


def test_available_power_is_1008_not_setpoint_echo_1006(tmp_path: Path) -> None:
    db = Observations(tmp_path / "unit.sqlite")
    try:
        for ioa, value in ((1006, 10.0), (1008, 25.0)):
            db.insert(
                "master",
                "PV01",
                10,
                dict(
                    event_type="master_observed_point",
                    ioa=ioa,
                    value=value,
                    available_at=10,
                    quality_valid=True,
                ),
            )
        features, _ = business_features(db, dict(station_id="PV01", telemetry_period_s=2), 20)
        assert features["available_kw"] == 25.0
    finally:
        db.close()
