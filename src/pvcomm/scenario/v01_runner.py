"""V0.1 real IEC 104 laboratory run orchestration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pvcomm.capture.tcpdump import TcpdumpCapture
from pvcomm.protocol.c104_runtime import C104Master, C104StationEndpoint
from pvcomm.scenario.v01_models import V01Config, v01_config_sha256


class RunRecorder:
    def __init__(self, run_dir: Path, config: V01Config, config_source: Path) -> None:
        self.run_dir = run_dir
        self.config = config
        self.config_source = config_source
        self.started_mono = time.monotonic()
        self._event_index = 0
        self._raw_index = 0
        self._event_file = (run_dir / "observed" / "events.jsonl").open("w", encoding="utf-8")
        self._master_file = (run_dir / "observed" / "master_events.jsonl").open(
            "w", encoding="utf-8"
        )
        self._terminal_file = (run_dir / "truth" / "terminal_events.jsonl").open(
            "w", encoding="utf-8"
        )
        self._electrical_file = (run_dir / "truth" / "electrical_state.jsonl").open(
            "w", encoding="utf-8"
        )
        self._raw_file = (run_dir / "observed" / "apdu_events.jsonl").open("w", encoding="utf-8")

    def _base(self) -> dict[str, object]:
        now = time.monotonic()
        return {
            "run_id": self.config.run_id,
            "event_id": f"evt-{self._event_index:08d}",
            "ts_event": datetime.now(UTC).isoformat(),
            "ts_mono_ns": time.monotonic_ns(),
            "sim_elapsed_ms": int((now - self.started_mono) * 1000),
        }

    def record_event(self, event_type: str, payload: dict[str, object]) -> None:
        self._event_index += 1
        event = {**self._base(), "event_type": event_type, **payload}
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        self._event_file.write(line)
        if event_type.startswith("master_"):
            self._master_file.write(line)
        if event_type.startswith("rtu_") or event_type.startswith("terminal_"):
            self._terminal_file.write(line)
        if event_type == "electrical_state":
            self._electrical_file.write(line)
        for stream in (
            self._event_file,
            self._master_file,
            self._terminal_file,
            self._electrical_file,
        ):
            stream.flush()

    def record_raw(self, direction: str, station_id: str, data: bytes) -> None:
        self._raw_index += 1
        now = time.monotonic()
        event = {
            "run_id": self.config.run_id,
            "raw_index": self._raw_index,
            "station_id": station_id,
            "direction": direction,
            "ts_capture_utc_ns": time.time_ns(),
            "ts_mono_ns": time.monotonic_ns(),
            "sim_elapsed_ms": int((now - self.started_mono) * 1000),
            "data_hex": data.hex(),
            "length": len(data),
        }
        self._raw_file.write(json.dumps(event, sort_keys=True) + "\n")
        self._raw_file.flush()

    def close(self) -> None:
        for stream in (
            self._event_file,
            self._master_file,
            self._terminal_file,
            self._electrical_file,
            self._raw_file,
        ):
            stream.close()


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
        self.run_dir.mkdir(parents=True)
        for relative in ("raw/pcap", "observed", "truth", "parsed", "labels", "qc"):
            (self.run_dir / relative).mkdir(parents=True)
        scenario_copy = self.run_dir / "scenario.yaml"
        scenario_copy.write_bytes(self.config_source.read_bytes())
        manifest: dict[str, Any] = {
            "run_id": self.config.run_id,
            "status": "created",
            "qc_status": "pending",
            "schema_version": self.config.schema_version,
            "config_sha256": v01_config_sha256(self.config),
            "scenario_sha256": _sha256(scenario_copy),
            "code_version": _git_sha(),
            "tools": {
                "tcpdump": _tool_version(["tcpdump", "--version"]),
                "tshark": _tool_version(["tshark", "--version"]),
            },
            "pcap": {},
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._write_manifest(manifest)
        recorder = RunRecorder(self.run_dir, self.config, self.config_source)
        capture = TcpdumpCapture(
            interface=self.config.capture_interface,
            output_path=self.run_dir / "raw" / "pcap" / "primary_loopback.pcap",
            capture_filter=self.config.capture_filter,
        )
        endpoints: list[C104StationEndpoint] = []
        master: C104Master | None = None
        capture_returncode: int | None = None
        try:
            manifest["status"] = "validating"
            self._write_manifest(manifest)
            capture.start()
            time.sleep(0.2)
            if not capture.process_running:
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
                endpoint.start()
                endpoints.append(endpoint)
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
            manifest["status"] = "completed"
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            if master is not None:
                master.stop()
            for endpoint in reversed(endpoints):
                endpoint.stop()
            capture_returncode = capture.stop()
            manifest["pcap"] = {
                "path": "raw/pcap/primary_loopback.pcap",
                "sha256": _sha256(capture.output_path) if capture.output_path.exists() else None,
                "size_bytes": capture.output_path.stat().st_size
                if capture.output_path.exists()
                else 0,
                "tcpdump_returncode": capture_returncode,
            }
            manifest["finished_at"] = datetime.now(UTC).isoformat()
            self._write_manifest(manifest)
            recorder.close()
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
