"""Real A01 runs. Independent frozen evidence is required for every invocation."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pvcomm.config.fault_scenes import a01_scenario
from pvcomm.config.faults import (
    FaultScope,
    GlobalScope,
    RegionScope,
    StationScope,
    instantiate_fault,
)
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.qc.fault import qc_fault
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet
from pvcomm.web.app import create_app

CASES: list[tuple[FaultScope, int, int]] = [
    (StationScope(kind="station", station_ids=("PV01",)), 5, 0),
    (StationScope(kind="station", station_ids=("PV01",)), 20, 1),
    (StationScope(kind="station", station_ids=("PV01", "PV02")), 5, 0),
    (RegionScope(kind="region", region_id="R1"), 20, 1),
    (GlobalScope(kind="global"), 60, 0),
]


@pytest.mark.lab
@pytest.mark.parametrize(
    "scope,duration,variant",
    CASES,
    ids=("single-low", "single-medium", "subset-low", "region-medium", "global-high"),
)
def test_real_a01(tmp_path: Path, scope: FaultScope, duration: int, variant: int) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux, retained weather and science/Arrow")
    template = a01_scenario(scope, duration, variant)
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    config = instantiate_fault(template, f"run_{uuid.uuid4().hex}")
    scenario = config.background
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
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
    assert not manifest["cleanup_errors"]
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    report = qc_fault(root)
    assert report["passed"], report
    assert report["label_quality"]["status"] == "not_evaluated"
    effects = report["communication_effects"]
    for row in effects["stations"].values():
        assert row["physical_samples_inside"] > 1
        assert row["maximum_physical_gap_s"] <= 1.5 * scenario.simulation.electrical_step_s
        if row["targeted"]:
            assert row["delivery_gap_observed"]
            assert row["master_measurements_inside"] == 0
            assert row["recovery_event"] is not None
            assert row["recovery_after_restoration_s"] >= 0
    if "PV03" not in config.target_stations():
        assessments = report["experiment_quality"]["command_assessments"]["PV03"]
        assert len(assessments) == 1 and assessments[0]["outcome"] == "accepted_complete"
    assert not report["experiment_quality"]["normal_regression"]["passed"]
    client = TestClient(create_app(tmp_path))
    listing = client.get("/api/runs").json()["runs"]
    assert len(listing) == 1
    cursor = effects["diagnostic_interval_utc_ns"][1] - 1
    response = client.get(f"/api/runs/{listing[0]['run_key']}/snapshot?at_ns={cursor}")
    assert response.status_code == 200
    assert all(
        word not in response.text
        for word in ("fault_contract", "A01", "single_fault_development", "event_1")
    )
    for row in response.json()["stations"]:
        if row["station_id"] in config.target_stations():
            assert int(row["power_available_at"]) < effects["diagnostic_interval_utc_ns"][0]
    with pytest.raises(FileExistsError):
        run_isolated(
            scenario.simulation.protocol,
            source,
            tmp_path,
            power=scenario.simulation,
            scenario=scenario,
            fault=config,
        )
