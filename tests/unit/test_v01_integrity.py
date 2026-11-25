"""Regression tests; synthetic unit fixtures never represent simulation output."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pvcomm.config.lab import load_v01_config
from pvcomm.protocol.c104_runtime import StationState
from pvcomm.storage.events import RunRecorder


def test_new_command_cannot_spend_time_before_arrival() -> None:
    station = load_v01_config(Path("configs/v0.1-lab-smoke.yaml")).stations[1]
    state = StationState(station, 90.0, 90.0, last_update_mono=0.0)
    assert state.accept(60.0, 100.0)
    state.advance(100.1)
    assert state.actual_kw == pytest.approx(88.0)
    assert not state.accept(float("nan"), 101.0)
    assert not state.accept(station.available_kw + 1, 101.0)
    assert state.accepted_setpoint_kw == 60.0


def test_storage_never_exposes_terminal_truth_and_records_unique_ids(tmp_path: Path) -> None:
    recorder = RunRecorder(tmp_path, "synthetic_test")

    def record(index: int) -> None:
        recorder.record_event("master_observed_point", {"value": index})
        recorder.record_event("terminal_command_accepted", {"hidden_test_value": index})
        recorder.record_raw("rtu_send", "PV01", b"synthetic unit fixture")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(100)))
    recorder.close()
    all_ids = []
    for path in tmp_path.rglob("*.jsonl"):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            all_ids.append(row["event_id"])
            if path.parent.name == "observed":
                assert "hidden_test_value" not in row
                assert row.get("direction") != "rtu_send"
                assert row["available_at"] == row["ts_event_utc_ns"]
    assert len(all_ids) == len(set(all_ids)) == 300


def test_endpoint_event_ids_are_unique_across_process_recorders(tmp_path: Path) -> None:
    for role in ("master", "rtu1", "rtu2", "rtu3"):
        recorder = RunRecorder(tmp_path, "synthetic_test", suffix=f"_{role}")
        recorder.record_event("rtu_started", {"station_id": role})
        recorder.close()
    event_ids = [
        json.loads(line)["event_id"]
        for path in tmp_path.rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert len(event_ids) == len(set(event_ids)) == 4


@pytest.mark.parametrize("extra", ["run_id: duplicate\n", "junk: &test [*test]\n"])
def test_lab_loader_rejects_duplicate_yaml_keys_and_aliases(tmp_path: Path, extra: str) -> None:
    text = Path("configs/v0.1-lab-smoke.yaml").read_text(encoding="utf-8")
    path = tmp_path / "bad.yaml"
    path.write_text(text + extra, encoding="utf-8")
    with pytest.raises(ValueError):
        load_v01_config(path)


def test_duplicate_station_id_and_boolean_period_are_rejected(tmp_path: Path) -> None:
    text = Path("configs/v0.1-lab-smoke.yaml").read_text(encoding="utf-8")
    for invalid in (
        text.replace("station_id: PV02", "station_id: PV01"),
        text.replace("telemetry_period_s: 2", "telemetry_period_s: true"),
    ):
        path = tmp_path / "bad.yaml"
        path.write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            load_v01_config(path)
