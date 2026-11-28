"""Real, small three-site normal cases; run only with explicit lab authorization."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from pvcomm.config.normal_scenes import SCENE_CODES, normal_scenario
from pvcomm.config.scenes import instantiate_scenario
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.qc.schedule import qc_schedule
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet, read_jsonl
from pvcomm.storage.power_parquet import export_power_parquet

CASES = [(code, variant) for code in SCENE_CODES for variant in (0, 1)] + [("N02", 2)]


@pytest.mark.lab
@pytest.mark.parametrize(("code", "variant"), CASES, ids=[f"{c}-{v}" for c, v in CASES])
def test_real_normal_scenario(tmp_path: Path, code: str, variant: int) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux, retained NASA input and science/Arrow")
    template = normal_scenario(code, variant)
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    scenario = instantiate_scenario(template, f"run_{uuid.uuid4().hex}")
    network_before = subprocess.check_output(["ip", "netns", "list"], text=True)
    root = run_isolated(
        scenario.simulation.protocol,
        source,
        tmp_path,
        power=scenario.simulation,
        scenario=scenario,
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["status"] == "completed" and not manifest["code_dirty"]
    assert manifest["cleanup_errors"] == []
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == network_before
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    report = qc_schedule(root)
    assert report["passed"], report
    rows = [
        r
        for r in read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
        if r.get("source") == "pvlib_pvwatts_inverter_v02" and r["sim_elapsed_ms"] >= 0
    ]
    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    pv01 = [r for r in rows if r["station_id"] == "PV01"]
    commands = [r for r in master if r["event_type"] == "master_command_sent"]
    if code in ("N01", "N02", "N03", "N08", "N10"):
        assert not commands
    if code == "N01":
        assert min(r["p_actual_kw"] for r in pv01) > 0
        assert all(r["optical_transmittance"] == 1 for r in rows)
    elif code == "N02":
        if variant == 0:
            assert all(r["p_actual_kw"] == r["p_avail_kw"] == 0 for r in rows)
        else:
            values = [r["p_avail_kw"] for r in pv01]
            assert min(values) == 0 and max(values) > 0
            ordered = sorted(pv01, key=lambda r: r["model_mono_ns"])
            assert (ordered[-1]["p_avail_kw"] > ordered[0]["p_avail_kw"]) == (variant == 1)
    elif code == "N03":
        minima = []
        for device in scenario.simulation.devices:
            own = [r for r in rows if r["station_id"] == device.station_id]
            low = min(own, key=lambda r: r["optical_transmittance"])
            minima.append(low["sim_elapsed_ms"])
            assert low["optical_transmittance"] < 0.7
            assert max(r["p_avail_kw"] for r in own) > min(r["p_avail_kw"] for r in own)
        assert minima == sorted(minima) and minima[-1] - minima[0] > 10_000
    elif code in ("N04", "N06", "N07", "N09"):
        assert {r["station_id"] for r in commands} == {"PV01"}
        for station in scenario.simulation.protocol.stations[1:]:
            assert all(
                r["p_set_accepted_kw"] == station.initial_setpoint_kw
                for r in rows
                if r["station_id"] == station.station_id
            )
        if code == "N04":
            target = scenario.expanded_schedule()[0].model_dump()["targets_kw"]["PV01"]
            assert any(abs(r["p_actual_kw"] - target) < 0.1 for r in pv01)
            assert min(r["p_avail_kw"] for r in pv01) > target
        elif code == "N06":
            assert len(commands) == 2
            assert min(r["p_actual_kw"] for r in pv01) < 12
            assert max(r["p_actual_kw"] for r in pv01 if r["sim_elapsed_ms"] > 120_000) > 15
        elif code == "N07":
            limited = [r for r in pv01 if r["sim_elapsed_ms"] > 60_000]
            assert all(r["p_set_accepted_kw"] > r["p_avail_kw"] + 1 for r in limited)
            assert all(abs(r["p_actual_kw"] - r["p_avail_kw"]) < 0.1 for r in limited)
        else:
            audit = report["experiment_quality"]["command_assessments"]["PV01"]
            assert len(audit) == 1 and audit[0]["outcome"] == "rejected"
            assert all(r["p_set_accepted_kw"] == 30.0 for r in pv01)
    elif code == "N05":
        assert {r["station_id"] for r in commands} == {"PV01", "PV02"}
        assert all(r["p_set_accepted_kw"] == 300.0 for r in rows if r["station_id"] == "PV03")
        assert commands[1]["sent_mono_ns"] - commands[0]["sent_mono_ns"] >= 3_000_000_000
    elif code == "N08":
        assert len(report["scheduled_business"]["tasks"][0]["interrogations"]) == 2
    elif code == "N10":
        assert report["normal_context"]["interruption_and_recovery"]["passed"]
        assert any(r["event_type"] == "master_recovery_interrogation" for r in master)
    with pytest.raises(FileExistsError):
        run_isolated(
            scenario.simulation.protocol,
            source,
            tmp_path,
            power=scenario.simulation,
            scenario=scenario,
        )
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == network_before
