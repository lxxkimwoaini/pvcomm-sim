"""Owned tcpdump process with readiness, draining and measured capture statistics."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TcpdumpCapture:
    interface: str
    output_path: Path
    capture_filter: str
    command_prefix: tuple[str, ...] = ()
    buffer_kib: int = 16384
    _process: subprocess.Popen[str] | None = field(default=None, init=False)
    _ready: threading.Event = field(default_factory=threading.Event, init=False)
    _reader: threading.Thread | None = field(default=None, init=False)
    _lines: list[str] = field(default_factory=list, init=False)
    stats: dict[str, int | None] = field(default_factory=dict, init=False)

    def start(self) -> None:
        if sys.platform != "linux":
            raise RuntimeError("tcpdump laboratory execution requires Linux")
        if self._process is not None:
            raise RuntimeError("capture already started")
        if self.output_path.exists():
            raise FileExistsError(f"capture already exists: {self.output_path}")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            *self.command_prefix,
            "tcpdump",
            "-n",
            "--immediate-mode",
            "-B",
            str(self.buffer_kib),
            "-i",
            self.interface,
            "-s",
            "0",
            "-U",
            "-w",
            str(self.output_path),
        ]
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

    def stop(self) -> int:
        process = self._process
        if process is None:
            return 0
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
        stderr = "".join(self._lines)
        self.stats["buffer_size_kib"] = self.buffer_kib
        for key, phrase in (
            ("packets_captured", "packets captured"),
            ("packets_received_by_filter", "packets received by filter"),
            ("packets_dropped_by_kernel", "packets dropped by kernel"),
        ):
            match = re.search(rf"(\d+) {phrase}", stderr)
            self.stats[key] = int(match[1]) if match else None
        self.output_path.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8")
        self._process = None
        return int(process.returncode or 0)

    @property
    def process_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
