"""Synthetic unit fixtures for V0.3 prerequisites (real lab separately marked)."""

import json
from pathlib import Path

import pytest

from pvcomm.clock import SceneClock
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import power_document
from pvcomm.qc.electrical import classify_feeder
from pvcomm.web.observation_index import ObservationIndex


def test_scene_weather_phase_does_not_include_initialization(tmp_path: Path) -> None:
    path = tmp_path / "scene.json"
    clock = SceneClock(path)
    assert clock.elapsed(1_000_000_000) == 0
    assert clock.elapsed(100_000_000_000) == 0
    path.write_text(json.dumps({"start_mono_ns": 105_000_000_000}))
    assert clock.elapsed(104_000_000_000) == 0
    assert clock.elapsed(115_123_456_789) == 10.123456789
    other = tmp_path / "paired.json"
    other.write_text(json.dumps({"start_mono_ns": 200_000_000_000}))
    assert SceneClock(other).elapsed(210_123_456_789) == clock.elapsed(115_123_456_789)


def test_observed_index_ingests_only_new_complete_bytes_and_never_truth(tmp_path: Path) -> None:
    path = tmp_path / "master.jsonl"
    row = {
        "event_id": "one",
        "event_type": "master_observed_point",
        "station_id": "PV01",
        "ioa": 1001,
        "value": 10.0,
        "available_at": 10,
        "truth_secret": "forbidden",
    }
    path.write_text(json.dumps(row) + "\n")
    index = ObservationIndex(tmp_path / "cache.sqlite")
    try:
        index.ingest(path)
        initial_bytes = index.ingested_bytes
        for _ in range(100):
            index.ingest(path)
        assert index.ingested_bytes == initial_bytes
        assert "truth_secret" not in index.rows(10)[0]
        assert index.rows(9) == []
        with path.open("a") as stream:
            stream.write(json.dumps({**row, "event_id": "two", "available_at": 20}) + "\n")
        index.ingest(path)
        assert len(index.rows(20)) == 2
        assert len(index.rows(10)) == 1
        assert index.rows(20, limit=1)[0]["event_id"] == "two"
    finally:
        index.connection.close()


def test_electrical_violation_and_reverse_flow_are_not_communication_labels() -> None:
    values = dict(
        bus_min_pu=0.89,
        bus_max_pu=1.11,
        line_max_loading_percent=110.0,
        transformer_loading_percent=120.0,
        grid_p_kw=-5.0,
        balance_p_kw=0.0,
        balance_q_kvar=0.0,
    )
    result = classify_feeder(values)
    assert result["calculation_valid"]
    assert result["reverse_flow"]
    assert len(result["electrical_violations"]) == 3
    assert "service_violation" not in result
    assert not classify_feeder({**values, "balance_p_kw": 1.0})["calculation_valid"]


@pytest.mark.parametrize("field", ["address", "feeder_id", "region_id", "station_id"])
def test_multisite_rejects_invalid_references(field: str) -> None:
    cfg = power_document(Path("configs/v0.3-prerequisite.yaml"))
    raw = cfg.model_dump(mode="json")
    raw["protocol"]["stations"][0][field] = "invalid"
    with pytest.raises(ValueError):
        V03Config.model_validate(raw)
