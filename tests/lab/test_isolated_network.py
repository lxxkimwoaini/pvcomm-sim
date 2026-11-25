"""Explicitly authorized real namespace/protocol experiment; never ordinary CI."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pvcomm.config.lab import load_v01_config
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.scenario.isolated_runner import run_isolated


@pytest.mark.lab
def test_real_three_process_endpoints_and_owned_cleanup(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires PVCOMM_ALLOW_LAB=1 in authorized Linux")
    before_routes = subprocess.check_output(["ip", "-j", "route"], text=True)
    before_ns = subprocess.check_output(["ip", "netns", "list"], text=True)
    path = Path("configs/v0.1-lab-smoke.yaml")
    config = load_v01_config(path)
    run_dir = run_isolated(config, path, tmp_path)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["cleanup_errors"] == []
    assert len(manifest["captures"]) == 4
    for capture in manifest["captures"]:
        assert capture["exit_code"] == 0
        assert capture["statistics"]["packets_captured"] > 0
        assert capture["statistics"]["packets_dropped_by_kernel"] == 0
    master = [
        json.loads(row)
        for row in (run_dir / "observed/master_events_master.jsonl").read_text().splitlines()
    ]
    assert len([row for row in master if row["event_type"] == "master_command_sent"]) == 6
    assert len([row for row in master if row.get("positive_confirmation") is True]) == 6
    for index in range(1, 4):
        terminal = [
            json.loads(row)
            for row in (run_dir / f"truth/terminal_events_rtu{index}.jsonl")
            .read_text()
            .splitlines()
        ]
        assert (
            len([row for row in terminal if row["event_type"] == "terminal_command_accepted"]) == 2
        )
    summary = parse_run(run_dir)
    assert summary["packet_count"] > summary["apdu_count"] > 0
    assert summary["incomplete_apdu_count"] == 0
    assert subprocess.check_output(["ip", "-j", "route"], text=True) == before_routes
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before_ns


@pytest.mark.lab
def test_sigterm_preserves_partial_evidence_and_cleans_owned_resources(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires PVCOMM_ALLOW_LAB=1 in authorized Linux")
    before_ns = subprocess.check_output(["ip", "netns", "list"], text=True)
    config = Path("configs/v0.1-lab-smoke.yaml")
    manifest_path = tmp_path / "v01_smoke_001/manifest.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pvcomm.cli",
            "run",
            "--isolated",
            "--config",
            str(config),
            "--output-root",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                if manifest["status"] == "running":
                    break
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("lab did not reach running before cancellation")
            time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=25)
        assert process.returncode == 130, (stdout, stderr)
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "cancelled"
        assert manifest["cleanup_errors"] == []
        assert manifest["captures"]
        assert subprocess.check_output(["ip", "netns", "list"], text=True) == before_ns
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=25)
