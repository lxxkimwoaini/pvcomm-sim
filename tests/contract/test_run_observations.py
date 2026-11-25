"""Explicit synthetic API fixtures, never official simulation evidence."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pvcomm.web.app import create_app


def fixture_run(root: Path) -> Path:
    run = root / "opaque_001"
    (run / "observed").mkdir(parents=True)
    (run / "truth").mkdir()
    shutil.copyfile("configs/v0.1-acceptance-30m.yaml", run / "scenario.yaml")
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "capture_topology": "isolated_master_P0_and_RTU_P1",
                "secret": "DO_NOT_EXPOSE",
            }
        )
    )
    (run / "truth/secret.jsonl").write_bytes(b"\xff\xff\xff")
    events = [
        {
            "run_id": "synthetic_fixture",
            "event_id": f"e{i}",
            "event_type": "master_observed_point",
            "station_id": "PV01",
            "ioa": 1001,
            "value": value,
            "available_at": at,
            "ts_event_utc_ns": at,
            "sim_elapsed_ms": i,
            "injection_secret": "DO_NOT_EXPOSE",
        }
        for i, (at, value) in enumerate(((1795590000000000001, 72.0), (1795590000000000009, 60.0)))
    ]
    (run / "observed/master_events_master.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in events), encoding="utf-8"
    )
    return run


def test_m2_whitelist_and_integer_nanosecond_causal_cursor(tmp_path: Path) -> None:
    fixture_run(tmp_path)
    client = TestClient(create_app(tmp_path))
    listed = client.get("/api/runs").json()["runs"]
    assert len(listed) == 1
    key = listed[0]["run_key"]
    response = client.get(f"/api/runs/{key}/snapshot?at_ns=1795590000000000001")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["cursor_utc_ns"] == "1795590000000000001"
    assert snapshot["stations"][0]["latest_power_kw"] == 72.0
    assert len(snapshot["events"]) == 1
    assert "DO_NOT_EXPOSE" not in response.text
    assert "injection_secret" not in response.text
    assert "payload_json" not in response.text
    before = client.get(f"/api/runs/{key}/snapshot?at_ns=0").json()
    assert before["events"] == []
    assert before["stations"][0]["latest_power_kw"] is None
    assert client.get(f"/api/runs/{key}/snapshot?mode=Oracle").status_code == 422
    assert client.get(f"/api/runs/{key}/truth").status_code == 404
    assert client.get("/api/runs/not-a-path/snapshot").status_code == 404


def test_only_unterminated_live_line_can_be_incomplete(tmp_path: Path) -> None:
    run = fixture_run(tmp_path)
    client = TestClient(create_app(tmp_path))
    key = client.get("/api/runs").json()["runs"][0]["run_key"]
    path = run / "observed/master_events_master.jsonl"
    original = path.read_text()
    path.write_text(original + '{"unfinished":')
    assert client.get(f"/api/runs/{key}/snapshot").status_code == 200
    path.write_text(original + '{"malformed":\n')
    assert client.get(f"/api/runs/{key}/snapshot").status_code == 409


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_unknown_run_root_is_empty(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "absent"))
    assert client.get("/api/runs").json() == {"runs": []}


def test_new_physical_curves_require_received_telemetry_and_respect_cursor(tmp_path: Path) -> None:
    run = fixture_run(tmp_path)
    path = run / "observed/master_events_master.jsonl"
    row = json.loads(path.read_text().splitlines()[1])
    for ioa, value in ((1003, 0.4), (1005, 700), (1006, 30), (1008, 24)):
        with path.open("a") as stream:
            stream.write(
                json.dumps({**row, "ioa": ioa, "value": value, "event_id": f"p{ioa}"}) + "\n"
            )
    # Invalid truth bytes are deliberately unreadable; API must not access them.
    (run / "truth/electrical_state_test.jsonl").write_bytes(b"\xff\xff")
    client = TestClient(create_app(tmp_path))
    key = client.get("/api/runs").json()["runs"][0]["run_key"]
    first = client.get(f"/api/runs/{key}/snapshot?at_ns=1795590000000000001").json()
    assert all(not m["points"] for m in first["stations"][0]["measurements"] if m["ioa"] != 1001)
    latest = client.get(f"/api/runs/{key}/snapshot").json()
    values = {
        m["ioa"]: m["points"][-1]["value"]
        for m in latest["stations"][0]["measurements"]
        if m["points"]
    }
    assert values[1005] == 700
    assert values[1008] == 24
    assert all(not m["points"] for m in latest["stations"][1]["measurements"])
