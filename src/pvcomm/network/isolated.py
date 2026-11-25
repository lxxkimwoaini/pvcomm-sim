"""Four owned namespaces with three direct links entirely inside the lab."""

from __future__ import annotations

import secrets
import subprocess
import sys


class IsolatedNetwork:
    def __init__(self) -> None:
        prefix = f"pvcl-{secrets.token_hex(5)}"
        self.master = f"{prefix}-master"
        self.rtus = tuple(f"{prefix}-rtu{i}" for i in range(1, 4))
        self.addresses = tuple(f"10.203.{i}.2" for i in range(1, 4))
        self._owned: list[str] = []

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["ip", *args], capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode:
            raise RuntimeError(f"ip {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def create(self) -> None:
        if sys.platform != "linux":
            raise RuntimeError("network namespace lab requires Linux")
        if self._owned:
            raise RuntimeError("lab network already created")
        try:
            for namespace in (self.master, *self.rtus):
                self._run("netns", "add", namespace)
                self._owned.append(namespace)
                self._run("-n", namespace, "link", "set", "lo", "up")
            for i, namespace in enumerate(self.rtus, start=1):
                self._run(
                    "-n",
                    self.master,
                    "link",
                    "add",
                    f"p0_{i}",
                    "type",
                    "veth",
                    "peer",
                    "name",
                    "p1",
                    "netns",
                    namespace,
                )
                self._run("-n", self.master, "addr", "add", f"10.203.{i}.1/30", "dev", f"p0_{i}")
                self._run("-n", self.master, "link", "set", f"p0_{i}", "up")
                self._run("-n", namespace, "addr", "add", f"10.203.{i}.2/30", "dev", "p1")
                self._run("-n", namespace, "link", "set", "p1", "up")
        except BaseException:
            self.close()
            raise

    def prefix(self, namespace: str) -> tuple[str, ...]:
        if namespace not in self._owned:
            raise ValueError("namespace is not owned by this run")
        return ("ip", "netns", "exec", namespace)

    def close(self) -> None:
        errors = []
        for namespace in reversed(self._owned.copy()):
            try:
                pids = self._run("netns", "pids", namespace).strip()
                if pids:
                    raise RuntimeError(f"namespace still has processes: {pids}")
                self._run("netns", "delete", namespace)
                self._owned.remove(namespace)
            except RuntimeError as exc:
                errors.append(f"{namespace}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
