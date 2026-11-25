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
from pvcomm.clock import ClockOrigin, SceneClock
from pvcomm.config.lab import V01Config, v01_config_sha256
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import V02Config
from pvcomm.network.isolated import IsolatedNetwork
from pvcomm.network.regional import RegionalNetwork
from pvcomm.storage.events import RunRecorder


def run_isolated(
    config: V01Config,
    source: Path,
    output_root: Path,
    *,
    power: V02Config | V03Config | None = None,
) -> Path:
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
    network = RegionalNetwork(power) if isinstance(power, V03Config) else IsolatedNetwork()
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

    if isinstance(network, RegionalNetwork):
        manifest["network"]["regions"] = network.regions
        manifest["network"]["topology"] = "region_bridge_aggregation_and_site_access"
    save_manifest()
    captures: list[TcpdumpCapture] = []
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[TextIO] = []
    failures: list[str] = []
    controller = RunRecorder(
        root,
        config.run_id,
        suffix="_controller",
        origin=origin,
        scene_clock=SceneClock(root / "scene.json") if isinstance(power, V03Config) else None,
    )
    feeder = None

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
        if power is not None:
            from pvcomm.power.runtime import FeederRuntime, prepare_power

            prepare_power(root, power)
            feeder = FeederRuntime(root, power, controller.record_event)
            manifest["data_status"] = "development_physical_cosimulation_not_dataset"
            manifest["dependencies"].update(
                {name: version(name) for name in ("pvlib", "pandapower")}
            )
            manifest["weather_sha256"] = power.weather.sha256
            manifest["power_config_sha256"] = hashlib.sha256(
                power.model_dump_json().encode()
            ).hexdigest()
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
        rtu_processes = [
            launch(f"rtu{i}", namespace) for i, namespace in enumerate(network.rtus, start=1)
        ]
        deadline = time.monotonic() + (30 if power is not None else 8)
        while not all(
            (root / f"rtu{i}.ready").exists() for i in range(1, len(config.stations) + 1)
        ):
            if any(p.poll() is not None for p in rtu_processes) or time.monotonic() > deadline:
                raise RuntimeError("RTUs not ready; see endpoint stderr logs")
            if feeder is not None:
                feeder.step()
            time.sleep(0.05)
        manifest["status"] = "running"
        save_manifest()
        master = launch("master", network.master)
        deadline = time.monotonic() + config.duration_s + 30
        interrupted = False
        resumed = False
        while master.poll() is None:
            if feeder is not None:
                feeder.step()
            if any(process.poll() is not None for process in processes[:-1]):
                raise RuntimeError("RTU process exited during experiment")
            if not all(capture.process_running for capture in captures):
                raise RuntimeError("capture process exited during experiment")
            if time.monotonic() > deadline:
                raise TimeoutError("master exceeded configured runtime and startup allowance")
            if (
                isinstance(power, V03Config)
                and (root / "master.initialized").exists()
                and not (root / "scene.json").exists()
            ):
                start_ns = time.monotonic_ns() + 500_000_000
                scene = {
                    "start_mono_ns": start_ns,
                    "start_utc_ns": origin.utc_ns(start_ns),
                    "weather_source_start": power.weather.source_start.isoformat(),
                    "rate": 1.0,
                }
                stage = root / "scene.json.tmp"
                stage.write_text(json.dumps(scene), encoding="utf-8")
                stage.rename(root / "scene.json")
                manifest["scene_clock"] = scene
                save_manifest()
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
