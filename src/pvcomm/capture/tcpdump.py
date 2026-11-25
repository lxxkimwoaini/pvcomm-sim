"""Small, explicit tcpdump lifecycle wrapper for local lab runs."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TcpdumpCapture:
    interface: str
    output_path: Path
    capture_filter: str
    _process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("capture already started")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "tcpdump",
            "-i",
            self.interface,
            "-s",
            "0",
            "-U",
            "-w",
            str(self.output_path),
        ]
        if self.capture_filter:
            command.extend(self.capture_filter.split())
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

    def stop(self) -> int:
        process = self._process
        if process is None:
            return 0
        if process.poll() is None:
            os.kill(-process.pid, signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.kill(-process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        self._process = None
        return int(process.returncode or 0)

    @property
    def process_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
