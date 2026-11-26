"""Owned P0/P1 endpoint smoke run in a network with no external routes."""

from __future__ import annotations

import hashlib
import json
import os
import platform
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
from pvcomm.config.faults import FaultScenarioConfig, instantiate_fault, load_fault_scenario
from pvcomm.config.lab import V01Config, v01_config_sha256
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import V02Config
from pvcomm.config.scenes import ScenarioConfig, canonical_hash, instantiate_scenario, load_scenario
from pvcomm.network.isolated import IsolatedNetwork
from pvcomm.network.regional import RegionalNetwork
from pvcomm.scenario.resources import ResourceSampler
from pvcomm.storage.events import RunRecorder


def run_isolated(
    config: V01Config,
    source: Path,
    output_root: Path,
    *,
    power: V02Config | V03Config | None = None,
    scenario: ScenarioConfig | None = None,
    fault: FaultScenarioConfig | None = None,
) -> Path:
    if sys.platform != "linux":
        raise RuntimeError("isolated IEC 104 laboratory run requires Linux")
    for name in ("ip", "tcpdump", "tshark", *(("tc",) if isinstance(power, V03Config) else ())):
        if shutil.which(name) is None:
            raise RuntimeError(f"lab tool is unavailable: {name}")
    dependencies = {name: version(name) for name in ("c104", "pydantic", "PyYAML")}
    if fault is not None:
        for name in ("bridge", "strace"):
            if shutil.which(name) is None:
                raise RuntimeError(f"lab tool is unavailable: {name}")
        if fault.background != scenario or not isinstance(power, V03Config):
            raise ValueError("fault and compiled scenario disagree")
        if instantiate_fault(load_fault_scenario(source), config.run_id) != fault:
            raise ValueError("fault source and effective document disagree")
    if scenario is not None:
        if scenario.simulation != power or scenario.simulation.protocol != config:
            raise ValueError("scenario and compiled simulation disagree")
        template = (
            load_scenario(source) if fault is None else load_fault_scenario(source).background
        )
        if instantiate_scenario(template, config.run_id) != scenario:
            raise ValueError("scenario source and effective document disagree")
    root = (output_root / config.run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    if scenario is None:
        (root / "scenario.yaml").write_bytes(source.read_bytes())
    else:
        diagnostic = root / "truth/scenario"
        diagnostic.mkdir(parents=True)
        (diagnostic / "source.yaml").write_bytes(
            source.read_bytes() if fault is None else template.model_dump_json(indent=2).encode()
        )
        (diagnostic / "effective.json").write_text(
            scenario.model_dump_json(indent=2), encoding="utf-8"
        )
        plan = [t.model_dump(mode="json") for t in scenario.expanded_schedule()]
        (diagnostic / "expanded-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        (root / "scenario.yaml").write_text(
            scenario.simulation.model_dump_json(indent=2), encoding="utf-8"
        )
    if fault is not None:
        diagnostic = root / "truth/fault"
        diagnostic.mkdir(parents=True)
        (diagnostic / "source.yaml").write_bytes(source.read_bytes())
        (diagnostic / "effective.json").write_text(
            fault.model_dump_json(indent=2), encoding="utf-8"
        )
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
    if scenario is not None:
        manifest["scenario_contract"] = {
            "version": scenario.schema_version,
            "source_sha256": hashlib.sha256(
                (root / "truth/scenario/source.yaml").read_bytes()
            ).hexdigest(),
            "effective_sha256": canonical_hash(scenario.model_dump(mode="json")),
            "plan_sha256": canonical_hash(plan),
            "scope": "Oracle",
        }
    if fault is not None:
        manifest["fault_contract"] = {
            "version": fault.schema_version,
            "scope": "Oracle",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "effective_sha256": canonical_hash(fault.model_dump(mode="json")),
        }

    def save_manifest() -> None:
        temp = root / "manifest.json.tmp"
        temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temp.replace(root / "manifest.json")

    if isinstance(network, RegionalNetwork):
        manifest["network"]["regions"] = network.regions
        manifest["network"]["topology"] = "region_bridge_aggregation_and_site_access"
        manifest["environment"] = {
            "platform": platform.platform(),
            "python": sys.version,
            "cpu_count": os.cpu_count(),
            "tc": subprocess.check_output(["tc", "-V"], text=True).strip(),
            "meminfo": Path("/proc/meminfo").read_text(),
            "output_filesystem": subprocess.check_output(["df", "-T", str(root)], text=True),
        }
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
    fault_controller = None
    socket_audit = None
    resources = ResourceSampler(root, origin) if isinstance(power, V03Config) else None

    def launch(role: str, namespace: str) -> subprocess.Popen[bytes]:
        log = (root / f"{role}.stderr.log").open("x", encoding="utf-8")
        logs.append(log)
        command = [
            sys.executable,
            "-m",
            "pvcomm.scenario.lab_worker",
            "--run-dir",
            str(root),
            "--role",
            role,
        ]
        if socket_audit is not None:
            command = socket_audit.command(role, command)
        process = subprocess.Popen(
            [*network.prefix(namespace), *command],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        processes.append(process)
        if socket_audit is not None:
            socket_audit.bind(role, process.pid)
        return process

    try:
        if fault is not None:
            from pvcomm.capture.socket_audit import SocketAudit

            socket_audit = SocketAudit(root)
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
        if fault is not None and isinstance(network, RegionalNetwork):
            from pvcomm.config.faults import LinkDelay
            from pvcomm.scenario.delay_fault import DelayFaultController
            from pvcomm.scenario.link_fault import LinkFaultController

            controller_type = (
                DelayFaultController if isinstance(fault.fault, LinkDelay) else LinkFaultController
            )
            fault_controller = controller_type(root, fault, network, origin)
        if isinstance(network, RegionalNetwork):
            manifest["network"]["netem_initial"] = network.shaping_evidence()
            save_manifest()
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
                native_spool=isinstance(power, V03Config),
                immediate_mode=not isinstance(power, V03Config),
                drain_on_stop=isinstance(power, V03Config),
            )
            captures.append(capture)
            capture.start()
        if isinstance(power, V03Config):
            manifest["capture_spools"] = [str(c.active_path) for c in captures]
            manifest["capture_persistence"] = (
                "native_linux_spool_verified_then_persistent_run_directory"
            )
            save_manifest()
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
            if fault_controller is not None:
                fault_controller.step()
            if resources is not None:
                resources.sample(
                    [p.pid for p in processes]
                    + [c.process_id for c in captures if c.process_id is not None]
                    + (socket_audit.tracer_pids() if socket_audit is not None else [])
                )
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
                maintenance_ready = (
                    not isinstance(power, V03Config)
                    or not event.planned
                    or (root / "master.maintenance-closed").exists()
                )
                if elapsed >= event.start_s and not interrupted and maintenance_ready:
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
        if fault_controller is not None:
            try:
                fault_controller.close()
                manifest["fault_contract"]["events_sha256"] = hashlib.sha256(
                    (root / "truth/fault/events.jsonl").read_bytes()
                ).hexdigest()
            except Exception as exc:
                failures.append(f"fault restoration: {exc}")
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
        if socket_audit is not None:
            try:
                manifest["socket_audit"] = socket_audit.finish()
            except Exception as exc:
                failures.append(f"socket audit finalization: {exc}")
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
        if isinstance(network, RegionalNetwork) and network.shaped:
            try:
                manifest["network"]["netem_final"] = network.shaping_evidence()
            except Exception as exc:
                failures.append(f"NetEm final evidence: {exc}")
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
