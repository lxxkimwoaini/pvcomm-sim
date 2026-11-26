"""Real c104, physical models and independent captures; no mock lab outcomes."""

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from pvcomm.capture.tcpdump import TcpdumpCapture
from pvcomm.config.lab import load_v01_config
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import power_document
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint, _set_callback_annotations
from pvcomm.qc.experiment import qc_experiment
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.events import RunRecorder
from pvcomm.storage.parquet import export_parquet, read_jsonl
from pvcomm.storage.power_parquet import export_power_parquet


@pytest.mark.lab
def test_planned_missing_availability_and_real_rejection_do_not_abort_other_tasks(
    tmp_path: Path,
) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip(
            "requires authorized Linux lab, retained NASA input and protocol/power/data groups"
        )
    path = Path("configs/v0.4a-prerequisite.yaml")
    config = power_document(path)
    assert isinstance(config, V03Config)
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    root = run_isolated(config.protocol, path, tmp_path, power=config)
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["status"] == "completed" and not manifest["code_dirty"]
    assert manifest["cleanup_errors"] == []
    master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
    allocations = [r for r in master if r["event_type"] == "master_dispatch_allocated"]
    assert len(allocations) == 2
    assert allocations[0]["excluded_observations"] == {"PV01": "stale"}
    assert allocations[0]["eligible_station_count"] == 2
    assert allocations[1]["excluded_observations"] == {}
    results = [r for r in master if r["event_type"] == "master_dispatch_station_result"]
    first = [r for r in results if r["dispatch_id"] == "prerequisite_1"]
    assert {r["station_id"]: r["outcome"] for r in first} == {
        "PV02": "negative_confirmation",
        "PV03": "accepted",
    }
    assert all(r["outcome"] == "accepted" for r in results if r["dispatch_id"] == "prerequisite_2")
    actual_negatives = [
        r
        for r in master
        if r["event_type"] == "master_command_confirmation" and r["positive_confirmation"] is False
    ]
    assert len(actual_negatives) == 1 and actual_negatives[0]["matched"]
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    report = qc_experiment(root)
    assert report["passed"], report
    assert not report["normal_regression"]["passed"]
    rejected = report["command_assessments"]["PV02"][0]
    assert rejected["outcome"] == "rejected" and rejected["transaction_evidence_valid"]
    assert rejected["negative_ack_apdu"] and rejected["rejection_event"]
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before


@pytest.mark.lab
def test_real_silent_terminal_times_out_without_fabricated_negative_confirmation(
    tmp_path: Path,
) -> None:
    """Real loopback protocol fixture, not a frozen A05 scenario or dual-end QC."""
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires the explicitly authorized Linux protocol lab")
    c104 = importlib.import_module("c104")
    config = load_v01_config(Path("configs/v0.1-lab-smoke.yaml"))
    recorder = RunRecorder(tmp_path, "v04a_real_protocol_fixture")
    endpoints = [
        C104StationEndpoint(s, "127.0.0.1", recorder.record_event, recorder.record_raw)
        for s in config.stations
    ]
    master = C104Master(
        "127.0.0.1",
        config.stations,
        recorder.record_event,
        recorder.record_raw,
        managed_lifecycle=True,
    )
    capture = TcpdumpCapture(
        "lo", tmp_path / "real-loopback-fixture.pcap", "tcp portrange 25041-25043"
    )

    def silence(point: Any, previous_info: Any, message: Any) -> Any:
        recorder.record_event("terminal_fixture_silent_command", {"station_id": "PV03"})
        return c104.ResponseState.NONE

    _set_callback_annotations(
        silence,
        {
            "point": c104.Point,
            "previous_info": c104.Information,
            "message": c104.IncomingMessage,
            "return": c104.ResponseState,
        },
    )
    endpoints[2].command_point.on_receive(silence)
    capture.start()
    try:
        for endpoint in endpoints:
            endpoint.start()
        master.start()
        master.wait_until_open()
        master.interrogate_all()
        started = time.monotonic()
        result = master.send_setpoint(
            config.stations[2], 10, "unit_timeout", raise_on_failure=False
        )
        elapsed = time.monotonic() - started
        assert result.outcome == "confirmation_timeout" and elapsed >= 3
        assert result.positive_confirmation is None and result.stack_result is False
        blocked = master.send_setpoint(
            config.stations[2], 10, "unit_repeat", raise_on_failure=False
        )
        assert blocked.outcome == "session_quarantined"
        healthy = master.send_setpoint(
            config.stations[0], 10, "unit_healthy", raise_on_failure=False
        )
        assert healthy.outcome == "accepted" and healthy.positive_confirmation is True
        (tmp_path / "protocol-results.json").write_text(
            json.dumps(
                {
                    "fixture_only": True,
                    "timeout_elapsed_s": elapsed,
                    "timeout_outcome": result.outcome,
                    "repeat_outcome": blocked.outcome,
                    "healthy_outcome": healthy.outcome,
                    "git_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], text=True
                    ).strip(),
                    "code_dirty": bool(
                        subprocess.check_output(["git", "status", "--porcelain"], text=True)
                    ),
                },
                indent=2,
            )
        )
    finally:
        try:
            master.stop()
        finally:
            for endpoint in endpoints:
                endpoint.stop()
            capture.stop()
            recorder.close()
    assert capture.stats["packets_dropped_by_kernel"] == 0
    master_rows = read_jsonl([tmp_path / "observed/master_events.jsonl"])
    assert not any(
        r["event_type"] == "master_command_confirmation" and r["station_id"] == "PV03"
        for r in master_rows
    )
    assert (tmp_path / "real-loopback-fixture.pcap").stat().st_size > 24
