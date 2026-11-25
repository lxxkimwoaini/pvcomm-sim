"""V0.1 real IEC 104 laboratory run orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from pvcomm.capture.tcpdump import TcpdumpCapture
from pvcomm.clock import ClockOrigin
from pvcomm.config.lab import V01Config, v01_config_sha256
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint
from pvcomm.storage.events import RunRecorder


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _tool_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return (
        (result.stdout or result.stderr).splitlines()[0]
        if (result.stdout or result.stderr)
        else None
    )


class V01Runner:
    def __init__(self, config: V01Config, config_source: Path, output_root: Path) -> None:
        self.config = config
        self.config_source = config_source
        self.output_root = output_root
        self.run_dir = output_root / config.run_id
        self._timeline_start: float | None = None
        if self.run_dir.exists():
            raise FileExistsError(f"run directory already exists: {self.run_dir}")

    def run(self) -> Path:
        if self.config.topology != "loopback":
            raise ValueError("isolated configuration requires --isolated")
        if sys.platform != "linux":
            raise RuntimeError("IEC 104 laboratory runs require authorized Linux")
        # Dependency failures must precede creation of an apparent run directory.
        version("c104")
        self.run_dir.mkdir(parents=True)
        for relative in ("raw/pcap", "observed", "truth", "parsed", "labels", "qc"):
            (self.run_dir / relative).mkdir(parents=True)
        scenario_copy = self.run_dir / "scenario.yaml"
        scenario_copy.write_bytes(self.config_source.read_bytes())
        (self.run_dir / "effective_config.json").write_text(
            self.config.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        origin = ClockOrigin.capture()
        git_status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout
        manifest: dict[str, Any] = {
            "run_id": self.config.run_id,
            "status": "created",
            "qc_status": "pending",
            "schema_version": self.config.schema_version,
            "config_sha256": v01_config_sha256(self.config),
            "scenario_sha256": _sha256(scenario_copy),
            "code_version": _git_sha(),
            "code_dirty": bool(git_status.strip()),
            "code_changed_paths": git_status.splitlines(),
            "clock_origin": asdict(origin),
            "data_status": "development_protocol_evidence_not_dataset",
            "capture_topology": "duplicate_loopback_not_independent_P0_P1",
            "dependencies": {name: version(name) for name in ("c104", "pydantic", "PyYAML")},
            "tools": {
                "tcpdump": _tool_version(["tcpdump", "--version"]),
                "tshark": _tool_version(["tshark", "--version"]),
            },
            "pcap": {},
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._write_manifest(manifest)
        recorder = RunRecorder(self.run_dir, self.config.run_id, origin=origin)
        primary_capture = TcpdumpCapture(
            interface=self.config.capture_interface,
            output_path=self.run_dir / "raw" / "pcap" / "primary_loopback.pcap",
            capture_filter=self.config.capture_filter,
        )
        diagnostic_capture = TcpdumpCapture(
            interface=self.config.capture_interface,
            output_path=self.run_dir / "raw" / "pcap" / "diagnostic_loopback.pcap",
            capture_filter=self.config.capture_filter,
        )
        captures = (primary_capture, diagnostic_capture)
        endpoints: list[C104StationEndpoint] = []
        master: C104Master | None = None
        try:
            manifest["status"] = "validating"
            self._write_manifest(manifest)
            for capture in captures:
                capture.start()
            if not all(capture.process_running for capture in captures):
                raise RuntimeError("tcpdump exited before the IEC 104 run started")
            self._timeline_start = time.monotonic()
            manifest["status"] = "running"
            self._write_manifest(manifest)
            for station in self.config.stations:
                endpoint = C104StationEndpoint(
                    config=station,
                    host=self.config.host,
                    record_event=recorder.record_event,
                    record_raw=recorder.record_raw,
                )
                endpoints.append(endpoint)
                endpoint.start()
            master = C104Master(
                host=self.config.host,
                stations=self.config.stations,
                record_event=recorder.record_event,
                record_raw=recorder.record_raw,
            )
            master.start()
            master.wait_until_open()
            master.interrogate_all()
            self._sleep_until(self.config.command_after_s)
            for station in self.config.stations:
                master.send_setpoint(station, self.config.command_kw, action="limit")
            self._sleep_until(self.config.release_after_s)
            for station in self.config.stations:
                master.send_setpoint(station, station.initial_setpoint_kw, action="release")
            self._sleep_until(self.config.duration_s)
            manifest["status"] = "finalizing"
        except KeyboardInterrupt:
            manifest["status"] = "cancelled"
            raise
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            cleanup_errors = []
            if master is not None:
                try:
                    master.stop()
                except Exception as exc:
                    cleanup_errors.append(f"master: {exc}")
            for endpoint in reversed(endpoints):
                try:
                    endpoint.stop()
                except Exception as exc:
                    cleanup_errors.append(f"RTU {endpoint.config.station_id}: {exc}")
            capture_results: dict[str, dict[str, object]] = {}
            for capture_name, capture in zip(("primary", "diagnostic"), captures, strict=True):
                try:
                    returncode = capture.stop()
                except Exception as exc:
                    cleanup_errors.append(f"capture {capture_name}: {exc}")
                    returncode = -1
                capture_results[capture_name] = {
                    "path": f"raw/pcap/{capture_name}_loopback.pcap",
                    "sha256": _sha256(capture.output_path)
                    if capture.output_path.exists()
                    else None,
                    "size_bytes": capture.output_path.stat().st_size
                    if capture.output_path.exists()
                    else 0,
                    "tcpdump_returncode": returncode,
                    "statistics": capture.stats,
                }
                if returncode != 0:
                    cleanup_errors.append(f"capture {capture_name} returned {returncode}")
            manifest["pcap"] = {
                "primary": capture_results["primary"],
                "diagnostic": capture_results["diagnostic"],
            }
            manifest["finished_at"] = datetime.now(UTC).isoformat()
            recorder.close()
            manifest["cleanup_errors"] = cleanup_errors
            if cleanup_errors:
                manifest["status"] = "failed"
            elif manifest["status"] == "finalizing":
                manifest["status"] = "completed"
            self._write_manifest(manifest)
        if manifest["status"] != "completed":
            raise RuntimeError(f"run failed during finalization: {manifest['cleanup_errors']}")
        return self.run_dir

    def _sleep_until(self, target_elapsed_s: int) -> None:
        deadline = time.monotonic() + max(0.0, target_elapsed_s - self._elapsed_s())
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))

    def _elapsed_s(self) -> float:
        # The run timeline starts immediately before endpoint startup.  The
        # small startup delay is intentionally included in the configured
        # wall-clock duration rather than hidden from the manifest.
        if self._timeline_start is None:
            self._timeline_start = time.monotonic()
        return time.monotonic() - self._timeline_start

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        path = self.run_dir / "manifest.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
