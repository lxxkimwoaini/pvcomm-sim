"""Synthetic metric tests, never reported as real research experiments."""

from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.experiments.baselines import (
    alert_episodes,
    benchmark_dataset,
    evaluate,
    temporal_scores,
)
from pvcomm.pipeline.dataset import write_json


def test_alert_episodes_do_not_cross_stations_or_runs() -> None:
    rows = [dict(run_id="r1", station_id="s1", window_end_utc_ns=t * 10**9) for t in (10, 15, 30)]
    rows.append(dict(run_id="r2", station_id="s1", window_end_utc_ns=16 * 10**9))
    assert len(alert_episodes(rows, [1] * 4, 10)) == 3


def test_one_alert_episode_cannot_detect_two_events() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    indices: list[dict[str, Any]] = [
        dict(
            run_id="unit",
            station_id="PV01",
            window_end_utc_ns=t * 10**9,
        )
        for t in (60, 65, 70, 75)
    ]
    events = [
        dict(run_id="unit", station_id="PV01", start_utc_ns=a * 10**9, end_utc_ns=b * 10**9)
        for a, b in ((59, 64), (69, 74))
    ]
    result = evaluate(np.array([1, 0, 1, 0]), np.ones(4), 0.5, indices, events, DatasetPolicy())
    assert result["detected_events"] == 1 and result["missed_events"] == 1
    assert len(result["event_outcomes"]) == 2
    assert result["event_outcomes"][0]["delay_s"] == 1


def test_temporal_state_resets_between_runs() -> None:
    np = pytest.importorskip("numpy")
    rows = [
        dict(run_id=r, station_id="PV01", window_end_utc_ns=t)
        for r, t in (("a", 1), ("a", 2), ("b", 1))
    ]
    scores = temporal_scores(np.array([[100.0], [0.0], [0.0]]), rows)
    assert scores.tolist() == [30.0, 21.0, 0.0]


@pytest.mark.parametrize("passed,digest", [(False, "wrong"), (True, "wrong")])
def test_training_refuses_failed_or_stale_independent_qc(
    tmp_path: Path, passed: bool, digest: str
) -> None:
    folder = tmp_path / "features-M2"
    folder.mkdir()
    write_json(folder / "quality.json", dict(passed=True))
    write_json(
        tmp_path / "leakage_report.json",
        dict(passed=passed, feature_quality_sha256=digest),
    )
    with pytest.raises(ValueError, match="independent dataset QC"):
        benchmark_dataset(tmp_path)
    assert not (tmp_path / "benchmark-M2").exists()
