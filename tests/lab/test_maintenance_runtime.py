"""Real c104 grouped maintenance, mixed recovery and independent service labels."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, RegionScope, StationScope, instantiate_fault
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import label_run
from pvcomm.qc.fault import qc_fault
from pvcomm.qc.labels import qc_labels
from pvcomm.qc.maintenance_acceptance import qc_grouped_recovery
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet


@pytest.mark.lab
@pytest.mark.parametrize("case", ["region", "mixed", "stations"])
def test_grouped_maintenance_real_recovery(tmp_path: Path, case: str) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux and retained historical weather")
    scope = (
        StationScope(kind="station", station_ids=("PV01", "PV03"))
        if case == "stations"
        else RegionScope(kind="region", region_id="R1")
    )
    raw = terminal_scenario("A08", scope, 3).model_dump(mode="json")
    if case == "mixed":
        raw["fault"]["scope"] = dict(kind="station", station_ids=["PV01"])
    maintained = set(raw["background"]["maintenance_windows"][0]["station_ids"])
    unaffected = {"PV01", "PV02", "PV03"} - maintained
    # A real command executes on an unaffected station during maintenance.
    raw["background"]["business_schedule"] = [
        t for t in raw["background"]["business_schedule"] if t["schedule_id"] == "recovery"
    ] + [
        dict(
            kind="setpoint",
            schedule_id="background",
            at_s=46,
            station_ids=sorted(unaffected),
            targets_kw={s: 20.0 for s in unaffected},
        )
    ]
    template = FaultScenarioConfig.model_validate(raw)
    source = tmp_path / "input.json"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    config = instantiate_fault(template, f"run_{uuid.uuid4().hex}")
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    routes = subprocess.check_output(["ip", "-j", "route"], text=True)
    root = run_isolated(
        config.background.simulation.protocol,
        source,
        tmp_path,
        power=config.background.simulation,
        scenario=config.background,
        fault=config,
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["status"] == "completed" and not manifest["code_dirty"]
    assert not manifest["cleanup_errors"]
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    assert subprocess.check_output(["ip", "-j", "route"], text=True) == routes
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    mechanism = qc_fault(root)
    assert mechanism["passed"], root / "qc"
    maintenance = mechanism["authorized_maintenance"]
    assert set(maintenance["stations"]) == maintained
    for station, evidence in maintenance["stations"].items():
        assert evidence["passed"]
        assert evidence["business_recovered_within_deadline"] is (
            station not in config.target_stations()
        )
    for station in unaffected:
        assessed = mechanism["experiment_quality"]["command_assessments"][station]
        assert len(assessed) == 1 and assessed[0]["outcome"] == "accepted_complete"
    label_run(root)
    quality = qc_labels(root)
    assert quality["passed"] and not quality["invalid_labels"] and not quality["uncertain_labels"]
    labels = [
        json.loads(line)
        for line in (root / "labels" / LABEL_VERSION / "event_labels.jsonl")
        .read_text()
        .splitlines()
    ]
    for label in labels:
        target = label["station_id"] in config.target_stations()
        assert label["service_violation"] is target
        assert label["fault_applied"] is target
        if target:
            assert not label["recovery_censored"]
    acceptance = qc_grouped_recovery(root)
    assert acceptance["passed"]
