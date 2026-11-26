"""Real small paired experiments; no raw events or packets are manufactured."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from pvcomm.config.pairs import development_pair
from pvcomm.scenario.pairs import run_pair


@pytest.mark.lab
@pytest.mark.parametrize("code", ["P03", "P04", "P06", "P03_recovery"])
def test_real_pair(tmp_path: Path, code: str) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires the authorized Linux lab and historical weather inputs")
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    routes = subprocess.check_output(["ip", "-j", "route"], text=True)
    config = development_pair(code, f"pair_{uuid.uuid4().hex}")
    root = run_pair(config, tmp_path)
    ledger = json.loads((root / "pair-manifest.json").read_bytes())
    assert ledger["status"] == "completed", root
    report = json.loads((root / "qc/v04-pair-qc-v1/report.json").read_bytes())
    assert report["passed"] and report["contrast_observed"], root
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    assert subprocess.check_output(["ip", "-j", "route"], text=True) == routes
