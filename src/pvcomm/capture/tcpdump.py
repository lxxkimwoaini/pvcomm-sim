"""Owned tcpdump process with readiness, draining and measured capture statistics."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TcpdumpCapture:
    interface: str
    output_path: Path
    capture_filter: str
    command_prefix: tuple[str, ...] = ()
    buffer_kib: int = 16384
    native_spool: bool = False
    immediate_mode: bool = True
    drain_on_stop: bool = False
    _spool: Path | None = field(default=None, init=False)
    _process: subprocess.Popen[str] | None = field(default=None, init=False)
    _ready: threading.Event = field(default_factory=threading.Event, init=False)
    _reader: threading.Thread | None = field(default=None, init=False)
    _lines: list[str] = field(default_factory=list, init=False)
    _statistics_ready: threading.Condition = field(default_factory=threading.Condition, init=False)
    _snapshots: list[dict[str, int]] = field(default_factory=list, init=False)
    stats: dict[str, int | None] = field(default_factory=dict, init=False)

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def active_path(self) -> Path:
        return self._spool or self.output_path

    def start(self) -> None:
        if sys.platform != "linux":
            raise RuntimeError("tcpdump laboratory execution requires Linux")
        if self._process is not None:
            raise RuntimeError("capture already started")
        if self.output_path.exists():
            raise FileExistsError(f"capture already exists: {self.output_path}")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.native_spool:
            self._spool = Path(tempfile.mkdtemp(prefix="pvcomm-pcap-")) / self.output_path.name
        command = [
            *self.command_prefix,
            "tcpdump",
            "-n",
            "-B",
            str(self.buffer_kib),
            "-i",
            self.interface,
            "-s",
            "0",
            "-U",
            "-w",
            str(self._spool or self.output_path),
        ]
        if self.immediate_mode:
            command.insert(len(self.command_prefix) + 1, "--immediate-mode")
        command.extend(("--", self.capture_filter))
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()
        if not self._ready.wait(timeout=5) or self._process.poll() is not None:
            self.stop()
            raise RuntimeError(f"tcpdump was not ready: {''.join(self._lines)[-2000:]}")

    def _drain(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for line in self._process.stderr:
            self._lines.append(line)
            if "listening on " in line:
                self._ready.set()
            if "packets dropped by kernel" in line:
                snapshot = self._statistics()
                if len(snapshot) == 3:
                    with self._statistics_ready:
                        self._snapshots.append(snapshot)
                        self._statistics_ready.notify_all()

    def _statistics(self) -> dict[str, int]:
        stderr = "".join(self._lines)
        result = {}
        for key, phrase in (
            ("packets_captured", "packets captured"),
            ("packets_received_by_filter", "packets received by filter"),
            ("packets_dropped_by_kernel", "packets dropped by kernel"),
        ):
            matches = re.findall(rf"(\d+) {phrase}", stderr)
            if matches:
                result[key] = int(matches[-1])
        return result

    def _wait_for_capture_drain(self, process: subprocess.Popen[str]) -> bool:
        # Opt-in only for the isolated Ethernet/any captures: Linux loopback
        # ps_recv can count both directions of a packet once captured by pcap.
        # Callers must first stop every owned traffic producer. SIGINT exits
        # tcpdump without processing all packets already buffered by libpcap.
        deadline = time.monotonic() + 5
        with self._statistics_ready:
            while process.poll() is None:
                previous = len(self._snapshots)
                process.send_signal(signal.Signals["SIGUSR1"])
                remaining = deadline - time.monotonic()

                def snapshot_ready(previous: int = previous) -> bool:
                    return len(self._snapshots) > previous

                if remaining <= 0 or not self._statistics_ready.wait_for(
                    snapshot_ready, timeout=remaining
                ):
                    return False
                snapshot = self._snapshots[-1]
                if (
                    snapshot["packets_captured"] + snapshot["packets_dropped_by_kernel"]
                    >= snapshot["packets_received_by_filter"]
                ):
                    return True
                # Statistics are acknowledged by the reader; poll only while
                # a measured backlog remains, bounded by the same deadline.
                self._statistics_ready.wait(timeout=min(0.05, max(0, deadline - time.monotonic())))
        return False

    def stop(self) -> int:
        process = self._process
        if process is None:
            return 0
        drain_complete = True
        if self.drain_on_stop and process.poll() is None:
            drain_complete = self._wait_for_capture_drain(process)
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=5)
        if process.stderr is not None:
            process.stderr.close()
        if self._spool is not None and self._spool.exists():
            # Finalize once, after capture exit. On a failed copy retain the
            # native spool; never remove the sole surviving PCAP evidence.
            with self._spool.open("rb") as source, self.output_path.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            if self.output_path.stat().st_size != self._spool.stat().st_size:
                raise RuntimeError(f"capture spool copy incomplete: {self._spool}")
            with self._spool.open("rb") as source, self.output_path.open("rb") as destination:
                if (
                    hashlib.file_digest(source, "sha256").digest()
                    != hashlib.file_digest(destination, "sha256").digest()
                ):
                    raise RuntimeError(f"capture spool hash mismatch: {self._spool}")
            self._spool.unlink()
            self._spool.parent.rmdir()
            self._spool = None
        stderr = "".join(self._lines)
        self.stats["buffer_size_kib"] = self.buffer_kib
        self.stats.update(self._statistics())
        if self.drain_on_stop:
            self.stats["drain_complete"] = int(drain_complete)
        self.output_path.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8")
        self._process = None
        if not drain_complete:
            raise RuntimeError(f"capture did not drain before its deadline: {self.output_path}")
        return int(process.returncode or 0)

    @property
    def process_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
