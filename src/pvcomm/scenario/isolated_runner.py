"""Owned P0/P1 endpoint smoke run in a network with no external routes."""

from __future__ import annotations

import hashlib
import json
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, TextIO

from pvcomm.capture.tcpdump import TcpdumpCapture
from pvcomm.clock import ClockOrigin
from pvcomm.config.lab import V01Config, v01_config_sha256
from pvcomm.network.isolated import IsolatedNetwork
from pvcomm.storage.events import RunRecorder


def run_isolated(config: V01Config, source: Path, output_root: Path) -> Path:
    if sys.platform != "linux":
        raise RuntimeError("isolated IEC 104 laboratory run requires Linux")
    for name in ("ip", "tcpdump", "tshark"):
        if shutil.which(name) is None:
            raise RuntimeError(f"lab tool is unavailable: {name}")
    dependencies = {name: version(name) for name in ("c104", "pydantic", "PyYAML")}
    root = (output_root / config.run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "scenario.yaml").write_bytes(source.read_bytes())
    (root / "effective_config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    network = IsolatedNetwork()
    origin = ClockOrigin.capture()
    metadata = {"clock_origin": asdict(origin), "addresses": network.addresses}
    (root / "runtime.json").write_text(json.dumps(metadata), encoding="utf-8")
    manifest: dict[str, Any] = {
        "run_id": config.run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "dependencies": dependencies,
        "tools": {
            name: subprocess.check_output(
                [name, "--version"], text=True, stderr=subprocess.DEVNULL
            ).splitlines()[0]
            for name in ("tcpdump", "tshark")
        },
        "status": "validating",
        "qc_status": "pending",
        "capture_topology": "isolated_master_P0_and_RTU_P1",
        "config_sha256": v01_config_sha256(config),
        "clock_origin": asdict(origin),
        "data_status": "development_protocol_evidence_not_dataset",
        "code_version": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "code_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "network": {
            "master": network.master,
            "rtus": network.rtus,
            "addresses": network.addresses,
            "default_route": None,
        },
    }

    def save_manifest() -> None:
        temp = root / "manifest.json.tmp"
        temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temp.replace(root / "manifest.json")

    save_manifest()
    captures: list[TcpdumpCapture] = []
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[TextIO] = []
    failures: list[str] = []
    controller = RunRecorder(root, config.run_id, suffix="_controller", origin=origin)

    def launch(role: str, namespace: str) -> subprocess.Popen[bytes]:
        log = (root / f"{role}.stderr.log").open("x", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            [
                *network.prefix(namespace),
                sys.executable,
                "-m",
                "pvcomm.scenario.lab_worker",
                "--run-dir",
                str(root),
                "--role",
                role,
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        processes.append(process)
        return process

    try:
        network.create()
        capture_specs = [
            ("primary", network.master, "any"),
            *(
                (f"diagnostic_rtu{i}", namespace, "p1")
                for i, namespace in enumerate(network.rtus, start=1)
            ),
        ]
        for name, namespace, interface in capture_specs:
            capture = TcpdumpCapture(
                interface,
                root / f"raw/pcap/{name}.pcap",
                config.capture_filter,
                command_prefix=network.prefix(namespace),
            )
            captures.append(capture)
            capture.start()
        for i, namespace in enumerate(network.rtus, start=1):
            process = launch(f"rtu{i}", namespace)
            deadline = time.monotonic() + 8
            while not (root / f"rtu{i}.ready").exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"rtu{i} was not ready; see its stderr log")
                time.sleep(0.05)
        manifest["status"] = "running"
        save_manifest()
        master = launch("master", network.master)
        deadline = time.monotonic() + config.duration_s + 30
        interrupted = False
        resumed = False
        while master.poll() is None:
            if any(process.poll() is not None for process in processes[:-1]):
                raise RuntimeError("RTU process exited during experiment")
            if not all(capture.process_running for capture in captures):
                raise RuntimeError("capture process exited during experiment")
            if time.monotonic() > deadline:
                raise TimeoutError("master exceeded configured runtime and startup allowance")
            event = config.interruption
            ready = root / "master.ready"
            if event is not None and ready.exists():
                elapsed = time.monotonic() - float(ready.read_text(encoding="ascii"))
                index = next(
                    i for i, s in enumerate(config.stations) if s.station_id == event.station_id
                )
                worker = processes[index]
                if elapsed >= event.start_s and not interrupted:
                    worker.send_signal(signal.SIGUSR1)
                    controller.record_event(
                        "terminal_interruption_requested",
                        {"station_id": event.station_id, "planned": event.planned},
                    )
                    interrupted = True
                if elapsed >= event.start_s + event.duration_s and interrupted and not resumed:
                    worker.send_signal(signal.SIGUSR2)
                    controller.record_event(
                        "terminal_recovery_requested", {"station_id": event.station_id}
                    )
                    resumed = True
            time.sleep(0.1)
        if master.returncode:
            raise RuntimeError(f"master returned {master.returncode}; see master.stderr.log")
        manifest["status"] = "finalizing"
    except KeyboardInterrupt:
        manifest["status"] = "cancelled"
        raise
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for process in reversed(processes):
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=8)
                if process.returncode and manifest["status"] != "cancelled":
                    failures.append(f"worker {process.pid}: exit {process.returncode}")
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                failures.append(f"worker {process.pid} required kill")
        capture_results = []
        for capture in reversed(captures):
            try:
                result = capture.stop()
                capture_results.append(
                    {
                        "path": str(capture.output_path.relative_to(root)),
                        "exit_code": result,
                        "statistics": capture.stats,
                        "sha256": hashlib.sha256(capture.output_path.read_bytes()).hexdigest()
                        if capture.output_path.exists()
                        else None,
                    }
                )
                if result:
                    failures.append(f"capture {capture.output_path.name}: exit {result}")
            except Exception as exc:
                failures.append(str(exc))
        for log in logs:
            log.close()
        controller.close()
        try:
            network.close()
        except Exception as exc:
            failures.append(str(exc))
        manifest["captures"] = list(reversed(capture_results))
        manifest["cleanup_errors"] = failures
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        if failures:
            manifest["status"] = "failed"
        elif manifest["status"] == "finalizing":
            manifest["status"] = "completed"
        save_manifest()
    if manifest["status"] != "completed":
        raise RuntimeError(f"run finalization failed: {failures}")
    return root
