"""Real c104 / namespace / pvlib / pandapower interruption prerequisite."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pvcomm.config.multisite import V03Config
from pvcomm.config.power import power_document
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.qc.power import evaluate_power
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet, read_jsonl
from pvcomm.storage.power_parquet import export_power_parquet


@pytest.mark.lab
def test_physics_continues_without_communication_and_recovery_is_current(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized Linux lab and retained historical input")
    path = Path("configs/v0.3-prerequisite.yaml")
    config = power_document(path)
    assert isinstance(config, V03Config)
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    root = run_isolated(config.protocol, path, tmp_path, power=config)
    terminal = read_jsonl(list((root / "truth").glob("terminal_events*.jsonl")))
    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    states = [
        r
        for r in read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
        if r.get("source") == "pvlib_pvwatts_inverter_v02" and r["station_id"] == "PV01"
    ]
    suspended = next(r["ts_mono_ns"] for r in terminal if r["event_type"] == "terminal_suspended")
    resumed = next(r["ts_mono_ns"] for r in terminal if r["event_type"] == "terminal_resumed")
    inside = [r for r in states if suspended <= r["model_mono_ns"] < resumed]
    assert len(inside) >= 15
    assert inside[-1]["model_mono_ns"] - inside[0]["model_mono_ns"] > 15e9
    assert abs(inside[-1]["ghi_w_m2"] - inside[0]["ghi_w_m2"]) > 1
    assert all(r["connected"] and not r["faulted"] for r in inside)
    assert len({r["command_count"] for r in inside}) == 1
    recovery = next(
        r
        for r in master
        if r["event_type"] == "master_observed_point"
        and r["station_id"] == "PV01"
        and r["ioa"] == 1001
        and r["ts_mono_ns"] >= resumed
    )
    physical = max(
        (r for r in states if r["model_mono_ns"] <= recovery["ts_mono_ns"]),
        key=lambda r: r["model_mono_ns"],
    )
    assert recovery["ts_mono_ns"] - physical["model_mono_ns"] < 2e9
    assert recovery["value"] == pytest.approx(physical["p_actual_kw"], abs=0.1)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "completed" and not manifest["cleanup_errors"]
    assert (
        manifest["scene_clock"]["weather_source_start"] == config.weather.source_start.isoformat()
    )
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    report = evaluate_power(root)
    (root / "prerequisite-qc.json").write_text(json.dumps(report, indent=2))
    assert report["passed"], [k for k, v in report["checks"].items() if not v["passed"]]
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
