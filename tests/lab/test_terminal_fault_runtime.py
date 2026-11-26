"""Real small RTU experiments with immutable evidence and independent QC."""

import json
import os
import subprocess
import sys
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
