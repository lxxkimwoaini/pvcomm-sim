"""Private fixed-role subprocess entry for the namespace smoke experiment."""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin, SceneClock
from pvcomm.config.lab import load_v01_config
from pvcomm.config.maintenance import StationMaintenance, legacy_maintenance
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint
from pvcomm.storage.events import RunRecorder


def publish_business_start(root: Path, start: float) -> None:
    """Publish the complete IPC value before the controller can observe readiness."""
    stage = root / "master.ready.tmp"
    with stage.open("x", encoding="ascii") as stream:
        stream.write(str(start))
    stage.rename(root / "master.ready")


def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("laboratory workers require Linux")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()
    root = args.run_dir
    config = load_v01_config(root / "scenario.yaml")
    power = None
    if (root / "power_config.json").exists():
        from pvcomm.config.power import power_document

        power = power_document(root / "scenario.yaml")
    from pvcomm.config.multisite import V03Config

    if args.role != "master" and args.role not in {
        f"rtu{i}" for i in range(1, len(config.stations) + 1)
    }:
        raise ValueError("unknown worker role")
    metadata = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    scenario_metadata = json.loads((root / "manifest.json").read_text())
    explicit_scenario = "scenario_contract" in scenario_metadata
    if (root / "truth/scenario").exists() and not explicit_scenario:
        raise ValueError("missing explicit scenario contract")
    origin = ClockOrigin(**metadata["clock_origin"])
    recorder = RunRecorder(
        root,
        config.run_id,
        suffix=f"_{args.role}",
        origin=origin,
        scene_clock=SceneClock(root / "scene.json") if isinstance(power, V03Config) else None,
    )
    stop = threading.Event()
    paused = threading.Event()

    def terminate(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    if args.role.startswith("rtu"):
        signal.signal(signal.SIGUSR1, lambda *_args: paused.set())
        signal.signal(signal.SIGUSR2, lambda *_args: paused.clear())
    endpoint: C104StationEndpoint | C104Master | None = None
    maintenance: list[threading.Thread] = []
    maintenance_errors: list[Exception] = []
    terminal_fault = None
    try:
        if args.role.startswith("rtu"):
            index = int(args.role[3:]) - 1
            if "fault_contract" in scenario_metadata:
                from pvcomm.config.faults import FaultScenarioConfig, TerminalFault
                from pvcomm.protocol.terminal_faults import TerminalFaultRuntime

                fault_config = FaultScenarioConfig.model_validate_json(
                    (root / "truth/fault/effective.json").read_bytes()
                )
                if (
                    isinstance(fault_config.fault, TerminalFault)
                    and config.stations[index].station_id in fault_config.target_stations()
                ):
                    terminal_fault = TerminalFaultRuntime(
                        root,
                        config.stations[index].station_id,
                        fault_config.fault,
                        config.stations[index].telemetry_period_s,
                        recorder.record_event,
                        stop,
                    )
            physical = None
            if power is not None:
                from pvcomm.power.runtime import PhysicalStationState, read_snapshot

                physical = PhysicalStationState(
                    root, power, power.devices[index], origin, recorder.record_event
                )
                while (
                    read_snapshot(root / f"truth/ipc/{power.devices[index].station_id}-pcc.jsonl")
                    is None
                ):
                    if stop.wait(1):
                        raise InterruptedError("power startup cancelled")
                    physical.advance(time.monotonic())
            endpoint = C104StationEndpoint(
                config.stations[index],
                metadata["addresses"][index],
                recorder.record_event,
                recorder.record_raw,
                state=physical,
                logical_elapsed=physical.elapsed
                if physical is not None and isinstance(power, V03Config)
                else None,
                fault=terminal_fault,
            )
            endpoint.start()
            (root / f"{args.role}.ready").touch(exist_ok=False)
            active = True
            next_tick = time.monotonic()
            while not stop.wait(0.05):
                if terminal_fault is not None:
                    terminal_fault.step()
                if power is not None and time.monotonic() >= next_tick:
                    endpoint.tick()
                    while next_tick <= time.monotonic():
                        next_tick += power.electrical_step_s
                if paused.is_set() and active:
                    endpoint.stop()
                    active = False
                    recorder.record_event(
                        "terminal_suspended", {"station_id": config.stations[index].station_id}
                    )
                    (root / f"{args.role}.suspended").touch(exist_ok=False)
                elif not paused.is_set() and not active:
                    endpoint.start()
                    active = True
                    recorder.record_event(
                        "terminal_resumed", {"station_id": config.stations[index].station_id}
                    )
                    (root / f"{args.role}.resumed").touch(exist_ok=False)
        else:
            scenario = None
            if explicit_scenario:
                from pvcomm.config.scenes import ScenarioConfig, canonical_hash

                scenario = ScenarioConfig.model_validate_json(
                    (root / "truth/scenario/effective.json").read_bytes()
                )
                if (
                    scenario.simulation != power
                    or canonical_hash(scenario.model_dump(mode="json"))
                    != scenario_metadata["scenario_contract"]["effective_sha256"]
                ):
                    raise ValueError("scenario effective contract mismatch")
            endpoint = C104Master(
                config.host,
                config.stations,
                recorder.record_event,
                recorder.record_raw,
                station_hosts=dict(
                    zip(
                        (station.station_id for station in config.stations),
                        metadata["addresses"],
                        strict=True,
                    )
                ),
                managed_lifecycle=isinstance(power, V03Config),
            )
            endpoint.start()
            endpoint.wait_until_open()
            endpoint.interrogate_all()
            if isinstance(power, V03Config):
                (root / "master.initialized").touch(exist_ok=False)
                while not (root / "scene.json").exists():
                    if stop.wait(0.05):
                        raise InterruptedError("scene startup cancelled")
                start_ns = json.loads((root / "scene.json").read_text())["start_mono_ns"]
                start = start_ns / 1e9
                if stop.wait(max(0, start - time.monotonic())):
                    raise InterruptedError("scene startup cancelled")
            else:
                start_ns = time.monotonic_ns()
                start = start_ns / 1e9
            interruption = config.interruption
            maintenance_events = (
                scenario.expanded_maintenance()
                if scenario is not None
                else legacy_maintenance(config)
            )
            for event in maintenance_events:
                if not event.planned:
                    continue
                recorder.record_event(
                    "master_maintenance_plan",
                    {
                        "station_id": event.station_id,
                        "maintenance_id": event.maintenance_id,
                        "window_start_utc_ns": origin.utc_ns(start_ns + event.start_s * 10**9),
                        "window_end_utc_ns": origin.utc_ns(
                            start_ns + (event.start_s + event.duration_s) * 10**9
                        ),
                        "recovery_deadline_s": 15,
                    },
                )
            publish_business_start(root, start)
            recorder.record_event("master_business_started", {})

            def wait_until(elapsed: float) -> None:
                if stop.wait(max(0, start + elapsed - time.monotonic())):
                    raise InterruptedError("lab worker cancelled")
                if maintenance_errors:
                    raise RuntimeError(
                        "planned maintenance handshake failed"
                    ) from maintenance_errors[0]

            if isinstance(power, V03Config):
                master_endpoint = endpoint

                def planned_maintenance(maintenance_event: StationMaintenance) -> None:
                    try:
                        wait_until(maintenance_event.start_s)
                        master_endpoint.suspend_for_maintenance(maintenance_event.station_id)
                        (root / maintenance_event.ready_file).touch(exist_ok=False)
                        wait_until(maintenance_event.start_s + maintenance_event.duration_s)
                        # Laboratory process lifecycle ordering, like initial rtu.ready.
                        # No electrical state or future response is read into observations.
                        index = next(
                            i
                            for i, site in enumerate(config.stations, 1)
                            if site.station_id == maintenance_event.station_id
                        )
                        resumed = root / f"rtu{index}.resumed"
                        resume_deadline = (
                            start + maintenance_event.start_s + maintenance_event.duration_s + 15
                        )
                        while not resumed.exists():
                            if time.monotonic() >= resume_deadline:
                                raise RuntimeError(
                                    "maintenance endpoint did not resume before recovery deadline"
                                )
                            if stop.wait(0.05):
                                raise InterruptedError("maintenance resume cancelled")
                        master_endpoint.resume_connection(maintenance_event.station_id)
                        if explicit_scenario:
                            wait_until(maintenance_event.start_s + maintenance_event.duration_s + 1)
                            master_endpoint.recover(
                                maintenance_event.station_id, raise_on_failure=False
                            )
                    except Exception as exc:
                        maintenance_errors.append(exc)
                        stop.set()

                for event in maintenance_events:
                    if event.planned:
                        thread = threading.Thread(
                            target=planned_maintenance,
                            args=(event,),
                            name=f"maintenance-{event.station_id}",
                        )
                        maintenance.append(thread)
                        thread.start()

            if explicit_scenario:
                from pvcomm.business.schedule import execute_schedule

                assert scenario is not None
                execute_schedule(scenario, endpoint, wait_until, recorder.record_event)
            elif isinstance(power, V03Config) and power.dispatch_tasks:
                from pvcomm.business.dispatch import execute_dispatches

                execute_dispatches(power, endpoint, wait_until, recorder.record_event)
            else:
                wait_until(config.command_after_s)
                for station in config.stations:
                    endpoint.send_setpoint(station, config.command_kw, "limit")
                if power is not None and power.repeat_command_after_s is not None:
                    wait_until(power.repeat_command_after_s)
                    for station in config.stations:
                        endpoint.send_setpoint(station, config.command_kw, "limit")
                wait_until(config.release_after_s)
                for station in config.stations:
                    endpoint.send_setpoint(station, station.initial_setpoint_kw, "release")
            if interruption is not None and not explicit_scenario:
                wait_until(interruption.start_s + interruption.duration_s + 1)
                endpoint.recover(
                    interruption.station_id, raise_on_failure=not isinstance(power, V03Config)
                )
            wait_until(config.duration_s)
            recorder.record_event("master_business_finished", {})
    finally:
        stop.set()
        try:
            for thread in maintenance:
                thread.join(timeout=8)
                if thread.is_alive():
                    raise RuntimeError("planned maintenance worker did not stop")
        finally:
            try:
                if endpoint is not None:
                    if terminal_fault is not None:
                        terminal_fault.restore("worker_finalization")
                    endpoint.stop()
            finally:
                recorder.close()


if __name__ == "__main__":
    main()
