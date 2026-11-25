"""Private fixed-role subprocess entry for the namespace smoke experiment."""

import argparse
import json
import signal
import threading
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin
from pvcomm.config.lab import load_v01_config
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint
from pvcomm.storage.events import RunRecorder


def main() -> None:
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

    def terminate(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
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
            stop.wait()
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

            def wait_until(elapsed: int) -> None:
                if stop.wait(max(0, start + elapsed - time.monotonic())):
                    raise InterruptedError("lab worker cancelled")

            wait_until(config.command_after_s)
            for station in config.stations:
                endpoint.send_setpoint(station, config.command_kw, "limit")
            wait_until(config.release_after_s)
            for station in config.stations:
                endpoint.send_setpoint(station, station.initial_setpoint_kw, "release")
            wait_until(config.duration_s)
    finally:
        if endpoint is not None:
            endpoint.stop()
        recorder.close()


if __name__ == "__main__":
    main()
