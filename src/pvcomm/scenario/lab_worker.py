"""Private fixed-role subprocess entry for the namespace smoke experiment."""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin
from pvcomm.config.lab import load_v01_config
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint
from pvcomm.storage.events import RunRecorder


def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("laboratory workers require Linux")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("master", "rtu1", "rtu2", "rtu3"), required=True)
    args = parser.parse_args()
    root = args.run_dir
    config = load_v01_config(root / "scenario.yaml")
    metadata = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    origin = ClockOrigin(**metadata["clock_origin"])
    recorder = RunRecorder(root, config.run_id, suffix=f"_{args.role}", origin=origin)
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
    try:
        if args.role.startswith("rtu"):
            index = int(args.role[-1]) - 1
            endpoint = C104StationEndpoint(
                config.stations[index],
                metadata["addresses"][index],
                recorder.record_event,
                recorder.record_raw,
            )
            endpoint.start()
            (root / f"{args.role}.ready").touch(exist_ok=False)
            active = True
            while not stop.wait(0.05):
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
            )
            endpoint.start()
            endpoint.wait_until_open()
            endpoint.interrogate_all()
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
            (root / "master.ready").write_text(str(start), encoding="ascii")
            recorder.record_event("master_business_started", {})

            def wait_until(elapsed: int) -> None:
                if stop.wait(max(0, start + elapsed - time.monotonic())):
                    raise InterruptedError("lab worker cancelled")

            wait_until(config.command_after_s)
            for station in config.stations:
                endpoint.send_setpoint(station, config.command_kw, "limit")
            wait_until(config.release_after_s)
            for station in config.stations:
                endpoint.send_setpoint(station, station.initial_setpoint_kw, "release")
            if interruption is not None:
                wait_until(interruption.start_s + interruption.duration_s + 1)
                endpoint.recover(interruption.station_id)
            wait_until(config.duration_s)
            recorder.record_event("master_business_finished", {})
    finally:
        if endpoint is not None:
            endpoint.stop()
        recorder.close()


if __name__ == "__main__":
    main()
