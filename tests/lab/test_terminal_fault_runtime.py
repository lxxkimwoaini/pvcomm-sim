"""Real small RTU experiments with immutable evidence and independent QC."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import GlobalScope, RegionScope, StationScope, instantiate_fault
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.qc.fault import qc_fault
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet


@pytest.mark.lab
@pytest.mark.parametrize(
    "code,strength,scope_kind,variant",
    [
        ("A04", 2, "station", 0),
        ("A04", 5, "region", 1),
        ("A04", 12, "global", 0),
        ("A05", 2, "station", 0),
        ("A05", 5, "region", 1),
        ("A05", 15, "global", 0),
        ("A06", 10, "station", 0),
        ("A06", 30, "region", 1),
        ("A06", 60, "global", 0),
        ("A08", 1, "station", 0),
        ("A08", 3, "station", 1),
        ("A08", 5, "station", 0),
    ],
    ids=[
        f"{code}-{level}"
        for code in ("A04", "A05", "A06", "A08")
        for level in ("low", "medium", "high")
    ],
)
def test_real_terminal_fault(
    tmp_path: Path, code: str, strength: int, scope_kind: str, variant: int
) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux and retained historical inputs")
    scope = (
        StationScope(kind="station", station_ids=("PV01",))
        if scope_kind == "station"
        else RegionScope(kind="region", region_id="R1")
        if scope_kind == "region"
        else GlobalScope(kind="global")
    )
    template = terminal_scenario(code, scope, strength, variant)
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    config = instantiate_fault(template, f"run_{uuid.uuid4().hex}")
    scenario = config.background
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    routes = subprocess.check_output(["ip", "-j", "route"], text=True)
    root = run_isolated(
        scenario.simulation.protocol,
        source,
        tmp_path,
        power=scenario.simulation,
        scenario=scenario,
        fault=config,
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["status"] == "completed" and not manifest["code_dirty"]
    assert manifest["cleanup_errors"] == []
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    assert subprocess.check_output(["ip", "-j", "route"], text=True) == routes
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    report = qc_fault(root)
    assert report["passed"], root / "qc"
    assert report["label_quality"]["status"] == "not_evaluated"
    if "PV03" not in config.target_stations():
        assert all(
            r["outcome"] == "accepted_complete"
            for r in report["experiment_quality"]["command_assessments"]["PV03"]
        )


@pytest.mark.lab
def test_terminal_cancel_restores_before_shutdown(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux")
    template = terminal_scenario("A06", StationScope(kind="station", station_ids=("PV01",)), 60)
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    identity = f"run_{uuid.uuid4().hex}"
    root = tmp_path / identity
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pvcomm.cli",
            "run-fault",
            "--config",
            str(source),
            "--output-root",
            str(tmp_path),
            "--run-id",
            identity,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 65
        path = root / "truth/fault/PV01.state.json"
        while not (path.exists() and json.loads(path.read_bytes())["phase"] == "feedback_hold"):
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 130, (stdout, stderr)
        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["status"] == "cancelled" and manifest["cleanup_errors"] == []
        state = json.loads(path.read_bytes())
        assert state["phase"] == "restored" and not state["suppressed_ioas"]
        events = [
            json.loads(line)
            for line in (root / "truth/fault/events.jsonl").read_text().splitlines()
        ]
        assert events[-1]["recovery_censored"] is True
        restored = next(r for r in events if r["event_type"] == "restore_verified")
        master = [
            json.loads(line)
            for line in (root / "observed/master_events_master.jsonl").read_text().splitlines()
        ]
        assert (
            next(r for r in master if r["event_type"] == "master_stopped")["ts_mono_ns"]
            >= restored["ts_mono_ns"]
        )
        assert all(r["statistics"]["packets_dropped_by_kernel"] == 0 for r in manifest["captures"])
        assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)


@pytest.mark.lab
def test_a05_pending_cancellation_preserves_protocol_and_model(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux")
    template = terminal_scenario("A05", StationScope(kind="station", station_ids=("PV01",)), 15)
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    identity = f"run_{uuid.uuid4().hex}"
    root = tmp_path / identity
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pvcomm.cli",
            "run-fault",
            "--config",
            str(source),
            "--output-root",
            str(tmp_path),
            "--run-id",
            identity,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 65
        paths: list[Path] = []
        while True:
            paths = list((root / "truth").glob("terminal_events*.jsonl"))
            rows = [json.loads(s) for p in paths for s in p.read_text().splitlines()]
            if any(r["event_type"] == "terminal_response_delay_started" for r in rows):
                break
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 130, (stdout, stderr)
        rows = [json.loads(s) for p in paths for s in p.read_text().splitlines()]
        assert not any(
            r["event_type"] == "terminal_command_accepted" and r["station_id"] == "PV01"
            for r in rows
        )
        assert any(
            r["event_type"] == "terminal_response_delay_finished" and r["cancelled"] for r in rows
        )
        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["status"] == "cancelled" and not manifest["cleanup_errors"]
        assert all(c["statistics"]["packets_dropped_by_kernel"] == 0 for c in manifest["captures"])
        assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)
