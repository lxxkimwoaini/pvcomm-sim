"""Owned worker syscall evidence for pre-write c104 callbacks; Oracle only."""

import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

VERSION = "0.4-socket-audit.1"


class SocketAudit:
    def __init__(self, root: Path) -> None:
        executable = shutil.which("strace")
        if executable is None:
            raise RuntimeError("A01 syscall evidence requires strace in the isolated Linux lab")
        self.executable = executable
        self.root = root
        self.directory = root / "truth/socket_audit"
        self.directory.mkdir(parents=True)
        self.version = subprocess.check_output([executable, "--version"], text=True).splitlines()[0]
        self.workers: dict[str, dict[str, int]] = {}

    def command(self, role: str, command: list[str]) -> list[str]:
        if re.fullmatch(r"master|rtu[1-9][0-9]*", role) is None:
            raise ValueError("socket audit role is not an owned worker")
        if role in self.workers or list(self.directory.glob(f"{role}.*")):
            raise FileExistsError("socket audit evidence already exists")
        # -D preserves the direct worker PID and its existing signal lifecycle.
        # The tracer lives in the SAME owned namespace, so -yy resolves sockets.
        return [
            self.executable,
            "-D",
            "-ff",
            "-ttt",
            "-xx",
            "-s",
            "65535",
            "-yy",
            "-e",
            "trace=sendto,connect,shutdown,close",
            "-o",
            str(self.directory / role),
            *command,
        ]

    def bind(self, role: str, pid: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = Path(f"/proc/{pid}/status")
            if not status.exists():
                break
            tracer = int(
                next(
                    s.split()[1]
                    for s in status.read_text().splitlines()
                    if s.startswith("TracerPid:")
                )
            )
            if tracer:
                self.workers[role] = {"worker_pid": pid, "tracer_pid": tracer}
                return
            time.sleep(0.01)
        raise RuntimeError(f"socket tracer did not attach to owned {role}")

    def tracer_pids(self) -> list[int]:
        return [r["tracer_pid"] for r in self.workers.values()]

    def finish(self) -> dict[str, Any]:
        if not self.workers:
            raise RuntimeError("socket audit has no owned workers")
        deadline = time.monotonic() + 5
        for worker in self.workers.values():
            status = Path(f"/proc/{worker['tracer_pid']}/stat")
            while status.exists():
                try:
                    state = status.read_text().rsplit(")", 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break  # Exited; the init process owns reaping the daemon.
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned socket tracer did not finish")
                time.sleep(0.01)
        files = []
        for role in self.workers:
            paths = sorted(self.directory.glob(f"{role}.*"))
            if not paths:
                raise RuntimeError("socket audit produced no evidence")
            for path in paths:
                content = path.read_bytes()
                last = content.splitlines()[-1] if content.splitlines() else b""
                completed = re.search(rb"\+\+\+ exited with ([0-9]+) \+\+\+$", last)
                if completed is None:
                    raise RuntimeError(f"socket audit is incomplete: {path.name}")
                files.append(
                    {
                        "role": role,
                        "path": str(path.relative_to(self.root)),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "trace_exit_code": int(completed[1]),
                    }
                )
        return {
            "version": VERSION,
            "scope": "Oracle",
            "tool": self.version,
            "workers": self.workers,
            "files": files,
            "completed": True,
        }
