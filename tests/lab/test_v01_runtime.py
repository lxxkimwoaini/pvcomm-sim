"""Real c104/TCP lab acceptance smoke test.

This test is intentionally excluded from ordinary CI and requires the
explicit Linux lab command with c104 and tcpdump installed.
"""

import json
import shutil
from pathlib import Path

import pytest

from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.scenario.v01_models import load_v01_config
from pvcomm.scenario.v01_runner import V01Runner


@pytest.mark.lab
def test_v01_three_station_real_runtime(tmp_path: Path) -> None:
    if shutil.which("tcpdump") is None:
        pytest.skip("tcpdump is required for the authorized Linux lab")
    config_path = Path("configs/v0.1-lab-smoke.yaml")
    config = load_v01_config(config_path)
    run_dir = V01Runner(config, config_path, tmp_path).run()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["pcap"]["tcpdump_returncode"] == 0
    master_events = [
        json.loads(line)
        for line in (run_dir / "observed" / "master_events.jsonl").read_text().splitlines()
    ]
    terminal_events = [
        json.loads(line)
        for line in (run_dir / "truth" / "terminal_events.jsonl").read_text().splitlines()
    ]
    assert sum(event["event_type"] == "master_interrogation" for event in master_events) == 3
    assert sum(event["event_type"] == "master_command_sent" for event in master_events) == 6
    assert sum(event["event_type"] == "terminal_command_accepted" for event in terminal_events) == 6
    parse_summary = parse_run(run_dir)
    assert parse_summary["apdu_count"] > 0
    assert parse_summary["object_count"] > 0
