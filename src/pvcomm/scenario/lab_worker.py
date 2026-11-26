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
    maintenance: threading.Thread | None = None
    maintenance_errors: list[Exception] = []
    try:
        if args.role.startswith("rtu"):
            index = int(args.role[3:]) - 1
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
            )
            endpoint.start()
            (root / f"{args.role}.ready").touch(exist_ok=False)
            active = True
            next_tick = time.monotonic()
            while not stop.wait(0.05):
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
        else:
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
                start = json.loads((root / "scene.json").read_text())["start_mono_ns"] / 1e9
                if stop.wait(max(0, start - time.monotonic())):
                    raise InterruptedError("scene startup cancelled")
            else:
                start = time.monotonic()
            interruption = config.interruption
            if interruption is not None and interruption.planned:
                recorder.record_event(
                    "master_maintenance_plan",
                    {
                        "station_id": interruption.station_id,
                        "window_start_utc_ns": origin.ts_utc_ns
                        + int((start + interruption.start_s) * 1e9)
                        - origin.ts_mono_ns,
                        "window_end_utc_ns": origin.ts_utc_ns
                        + int((start + interruption.start_s + interruption.duration_s) * 1e9)
                        - origin.ts_mono_ns,
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

            if isinstance(power, V03Config) and interruption is not None and interruption.planned:
                master_endpoint = endpoint
                maintenance_event = interruption

                def planned_maintenance() -> None:
                    try:
                        wait_until(maintenance_event.start_s)
                        master_endpoint.suspend_for_maintenance(maintenance_event.station_id)
                        (root / "master.maintenance-closed").touch(exist_ok=False)
                        wait_until(maintenance_event.start_s + maintenance_event.duration_s)
                        master_endpoint.resume_connection(maintenance_event.station_id)
                    except Exception as exc:
                        maintenance_errors.append(exc)
                        stop.set()

                maintenance = threading.Thread(
                    target=planned_maintenance, name="planned-maintenance"
                )
                maintenance.start()

            if isinstance(power, V03Config) and power.dispatch_tasks:
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
            if interruption is not None:
                wait_until(interruption.start_s + interruption.duration_s + 1)
                endpoint.recover(
                    interruption.station_id, raise_on_failure=not isinstance(power, V03Config)
                )
            wait_until(config.duration_s)
            recorder.record_event("master_business_finished", {})
    finally:
        stop.set()
        try:
            if maintenance is not None:
                maintenance.join(timeout=8)
                if maintenance.is_alive():
                    raise RuntimeError("planned maintenance worker did not stop")
        finally:
            try:
                if endpoint is not None:
                    endpoint.stop()
            finally:
                recorder.close()


if __name__ == "__main__":
    main()
