"""Owned localhost API measurement helper, run only by the explicit lab harness."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin
from pvcomm.scenario.resources import ResourceSampler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sampler = ResourceSampler(
        args.output, ClockOrigin.capture(), scope="web_api_and_measurement_helper_only"
    )
    with (args.output / "api.log").open("x") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "pvcomm.web.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--no-access-log",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline and not (args.output / "stop").exists():
                if process.poll() is not None:
                    raise RuntimeError(f"API exited: {process.returncode}")
                sampler.sample([process.pid])
                time.sleep(1)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
