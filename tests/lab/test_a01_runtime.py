"""Real A01 runs. Independent frozen evidence is required for every invocation."""

import hashlib
import json
import os
import subprocess
import sys
import time
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
from pvcomm.pipeline.iec104_parser import PARSER_VERSION, parse_run
from pvcomm.qc.fault import VERSION, qc_fault
from pvcomm.qc.schedule import audit_schedule
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet, read_jsonl
from pvcomm.storage.power_parquet import export_power_parquet
from pvcomm.web.app import create_app

CASES: list[tuple[FaultScope, int, int]] = [
    (StationScope(kind="station", station_ids=("PV01",)), 5, 0),
    (StationScope(kind="station", station_ids=("PV01",)), 20, 1),
    (StationScope(kind="station", station_ids=("PV01", "PV02")), 5, 0),
    (RegionScope(kind="region", region_id="R1"), 20, 1),
    (GlobalScope(kind="global"), 60, 0),
]


def run_cli_evidence(directory: Path, name: str, *arguments: str) -> None:
    command = [sys.executable, "-m", "pvcomm.cli", *arguments]
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    (directory / f"cli-{name}.json").write_text(
        json.dumps(
            {
                "command": command,
                "exit_code": result.returncode,
                "elapsed_s": time.monotonic() - started,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


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
    cli_case = scope.kind == "station" and len(scope.station_ids) == 1 and duration == 5
    if cli_case:
        run_cli_evidence(tmp_path, "validate", "validate-fault-scenario", "--config", str(source))
        run_cli_evidence(
            tmp_path,
            "run",
            "run-fault",
            "--config",
            str(source),
            "--output-root",
            str(tmp_path),
            "--run-id",
            scenario.simulation.protocol.run_id,
        )
        root = tmp_path / scenario.simulation.protocol.run_id
    else:
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
    if cli_case:
        run_cli_evidence(tmp_path, "parse", "parse", "--run-dir", str(root), "--parquet")
        run_cli_evidence(tmp_path, "qc", "qc", "--run-dir", str(root), "--profile", "fault")
        report = json.loads((root / "qc" / VERSION / "report.json").read_bytes())
    else:
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
    # A first post-restoration measurement can still be queued old data. Check
    # the independently scheduled GI transaction, not that first value alone.
    background_business = audit_schedule(
        scenario,
        read_jsonl(list((root / "observed").glob("master_events*.jsonl"))),
        read_jsonl(list((root / "truth").glob("terminal_events*.jsonl"))),
        read_jsonl([root / "parsed" / PARSER_VERSION / "iec104_objects.jsonl"]),
        report["experiment_quality"]["command_assessments"],
        [
            r
            for r in read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
            if r.get("source") == "pvlib_pvwatts_inverter_v02"
        ],
    )
    with (root / "qc/a01-background-schedule.json").open("x", encoding="utf-8") as stream:
        json.dump(background_business, stream, indent=2)
    assert background_business["passed"], background_business
    assert not report["experiment_quality"]["normal_regression"]["passed"]
    client = TestClient(create_app(tmp_path))
    listing = client.get("/api/runs").json()["runs"]
    assert len(listing) == 1
    cursor = effects["diagnostic_interval_utc_ns"][1] - 1
    response = client.get(f"/api/runs/{listing[0]['run_key']}/snapshot?at_ns={cursor}")
    assert response.status_code == 200
    assert all(
        word not in response.text
        for word in ("fault_contract", "A01", "single_fault_development", "event_1", "socket_audit")
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


@pytest.mark.lab
def test_a01_sigterm_restores_before_endpoint_shutdown(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux, retained weather and science/Arrow")
    config = a01_scenario(RegionScope(kind="region", region_id="R1"), 60)
    source = tmp_path / "input.yaml"
    source.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    identity = f"run_{uuid.uuid4().hex}"
    root = tmp_path / identity
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    routes = subprocess.check_output(["ip", "-j", "route"], text=True)
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
        deadline = time.monotonic() + 60
        journal = root / "truth/fault/events.jsonl"
        while True:
            if journal.exists() and '"event_type": "fault_active"' in journal.read_text():
                break
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("A01 did not reach verified active state before cancellation")
            time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 130, (stdout, stderr)
        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["status"] == "cancelled" and manifest["cleanup_errors"] == []
        assert (
            manifest["fault_contract"]["events_sha256"]
            == hashlib.sha256(journal.read_bytes()).hexdigest()
        )
        assert all(
            c["exit_code"] == 0 and c["statistics"]["packets_dropped_by_kernel"] == 0
            for c in manifest["captures"]
        )
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        restored = [r for r in events if r["event_type"] == "restore_verified"]
        assert len(restored) == 1 and restored[0]["reason"] == "run_finalization"
        assert restored[0]["before"]["state"] == "disabled"
        assert restored[0]["after"]["state"] == "forwarding"
        stopped = [
            json.loads(line)
            for line in (root / "observed/master_events_master.jsonl").read_text().splitlines()
            if json.loads(line)["event_type"] == "master_stopped"
        ]
        assert len(stopped) == 1 and stopped[0]["ts_mono_ns"] >= restored[0]["ts_mono_ns"]
        assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
        assert subprocess.check_output(["ip", "-j", "route"], text=True) == routes
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)
