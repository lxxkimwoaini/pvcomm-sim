"""Real owned NetEm/IEC104 evidence. Never use a mock as experiment output."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from pvcomm.config.fault_scenes import a02_scenario
from pvcomm.config.faults import (
    FaultScope,
    GlobalScope,
    LinkDelay,
    RegionScope,
    StationScope,
    instantiate_fault,
)
from pvcomm.network.regional import RegionalNetwork
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.qc.fault import qc_fault
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet


def require_lab() -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux with retained scientific inputs")


@pytest.mark.lab
@pytest.mark.parametrize("direction", ("master_to_rtu", "rtu_to_master"))
def test_netem_change_preserves_queued_datagrams(tmp_path: Path, direction: str) -> None:
    require_lab()
    config = a02_scenario(StationScope(kind="station", station_ids=("PV01",)), 1000, direction)
    assert isinstance(config.fault, LinkDelay)
    network = RegionalNetwork(config.background.simulation, delay_fault=config.fault)
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    receiver = sender = None
    snapshots = []
    try:
        network.create()
        port = network.delay_ports(config.fault.scope, direction)[0]
        reverse = direction == "rtu_to_master"
        destination = "10.204.1.1" if reverse else "10.204.1.2"
        receiver_code = (
            "import socket,json,time,pathlib; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
            f"s.bind(('{destination}',18002)); s.settimeout(8);"
            "pathlib.Path(__import__('sys').argv[1]).write_text('ready'); rows=[]\n"
            "for i in range(60):\n"
            " data=s.recv(1024); rows.append([json.loads(data),time.time_ns()])\n"
            "print(json.dumps(rows))"
        )
        receiver = subprocess.Popen(
            [
                *network.prefix(network.master if reverse else network.rtus[0]),
                sys.executable,
                "-c",
                receiver_code,
                str(tmp_path / "ready"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists():
            assert receiver.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        sender_code = (
            "import socket,json,time; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
            "for i in range(60):\n"
            f" s.sendto(json.dumps([i,time.time_ns()]).encode(),('{destination}',18002));"
            " time.sleep(.03)"
        )
        sender = subprocess.Popen(
            [
                *network.prefix(network.rtus[0] if reverse else network.master),
                sys.executable,
                "-c",
                sender_code,
            ]
        )
        time.sleep(0.35)
        snapshots.append(network.delay_evidence(*port))
        snapshots.append(network.change_delay(*port, 1000))
        time.sleep(0.4)
        snapshots.append(network.delay_evidence(*port))
        assert snapshots[-1]["actual"]["qlen"] > 0
        snapshots.append(network.change_delay(*port, 0))
        stdout, stderr = receiver.communicate(timeout=10)
        sender.wait(timeout=5)
        assert receiver.returncode == sender.returncode == 0, stderr
        rows = json.loads(stdout)
        snapshots.append(network.delay_evidence(*port))
        (tmp_path / "probe.json").write_text(
            json.dumps(
                {
                    "snapshots": snapshots,
                    "datagrams": rows,
                    "kernel": subprocess.check_output(["uname", "-r"], text=True).strip(),
                    "tc": subprocess.check_output(["tc", "-V"], text=True).strip(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert sorted(r[0][0] for r in rows) == list(range(60))
        assert all(r["actual"]["drops"] == 0 for r in snapshots)
        assert snapshots[-1]["actual"]["qlen"] == 0
        assert snapshots[-1]["actual"]["options"] == snapshots[0]["actual"]["options"]
        assert any((r[1] - r[0][1]) / 1e9 > 0.9 for r in rows)
    finally:
        for process in (sender, receiver):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        network.close()
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before


CASES: list[tuple[FaultScope, int, str, int]] = [
    (StationScope(kind="station", station_ids=("PV01",)), 200, "master_to_rtu", 0),
    (StationScope(kind="station", station_ids=("PV01",)), 1000, "rtu_to_master", 1),
    (StationScope(kind="station", station_ids=("PV01",)), 3000, "master_to_rtu", 0),
    (StationScope(kind="station", station_ids=("PV01", "PV02")), 200, "rtu_to_master", 1),
    (RegionScope(kind="region", region_id="R1"), 1000, "master_to_rtu", 0),
    (GlobalScope(kind="global"), 3000, "rtu_to_master", 1),
]


@pytest.mark.lab
@pytest.mark.parametrize(
    "scope,delay,direction,variant",
    CASES,
    ids=(
        "single-low",
        "single-medium",
        "single-high",
        "subset-low",
        "region-medium",
        "global-high",
    ),
)
def test_real_a02(
    tmp_path: Path, scope: FaultScope, delay: int, direction: str, variant: int
) -> None:
    require_lab()
    template = a02_scenario(scope, delay, direction, variant)
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
    assert manifest["status"] == "completed" and manifest["code_dirty"] is False
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
        commands = report["experiment_quality"]["command_assessments"]["PV03"]
        assert len(commands) == 1 and commands[0]["outcome"] == "accepted_complete"
    assert all(r["passed"] for r in report["physical_continuity"].values())
    assert report["recovery"]["passed"]


@pytest.mark.lab
def test_a02_cancel_restores_before_endpoint_shutdown(tmp_path: Path) -> None:
    require_lab()
    template = a02_scenario(RegionScope(kind="region", region_id="R1"), 3000, "rtu_to_master")
    source = tmp_path / "input.yaml"
    source.write_text(template.model_dump_json(indent=2), encoding="utf-8")
    identity = f"run_{uuid.uuid4().hex}"
    root = tmp_path / identity
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
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
        while not (journal.exists() and '"event_type": "fault_active"' in journal.read_text()):
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 130, (stdout, stderr)
        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["status"] == "cancelled" and manifest["cleanup_errors"] == []
        events = [json.loads(r) for r in journal.read_text().splitlines()]
        restored = [r for r in events if r["event_type"] == "restore_verified"]
        assert len(restored) == 1
        assert restored[0]["before"]["actual"]["options"]["delay"]["delay"] > 3
        assert restored[0]["after"]["actual"]["options"]["delay"]["delay"] < 0.15
        assert events[-1]["recovery_censored"] is True
        stopped = [
            json.loads(r)
            for r in (root / "observed/master_events_master.jsonl").read_text().splitlines()
            if json.loads(r)["event_type"] == "master_stopped"
        ]
        assert len(stopped) == 1 and stopped[0]["ts_mono_ns"] >= restored[0]["ts_mono_ns"]
        assert all(
            r["exit_code"] == 0 and r["statistics"]["packets_dropped_by_kernel"] == 0
            for r in manifest["captures"]
        )
        assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=30)
