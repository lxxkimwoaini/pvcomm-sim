"""Owned Linux process resource samples; infrastructure diagnostics only."""

import os
import sys
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin
from pvcomm.power.runtime import append_snapshot


class ResourceSampler:
    def __init__(
        self, root: Path, origin: ClockOrigin, *, scope: str = "controller_workers_capture_only"
    ) -> None:
        self.root, self.origin = root, origin
        self.scope = scope
        self.previous: tuple[float, float] | None = None
        self.last = 0.0

    def sample(self, pids: list[int]) -> None:
        if sys.platform == "win32":
            raise RuntimeError("resource sampler requires Linux /proc")
        now = time.monotonic()
        if now - self.last < 1:
            return
        cpu_s, rss_bytes, read_bytes, write_bytes = 0.0, 0, 0, 0
        active = []
        for pid in set([os.getpid(), *pids]):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                cpu_s += (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
                rss_bytes += int(stat[21]) * os.sysconf("SC_PAGE_SIZE")
                io = dict(
                    line.split(": ", 1) for line in Path(f"/proc/{pid}/io").read_text().splitlines()
                )
                read_bytes += int(io["read_bytes"])
                write_bytes += int(io["write_bytes"])
                active.append(pid)
            except FileNotFoundError:
                continue  # A process may finish between poll and sampling.
        cpu_percent = (
            None
            if self.previous is None
            else max(0.0, (cpu_s - self.previous[1]) / (now - self.previous[0]) * 100)
        )
        mono = time.monotonic_ns()
        append_snapshot(
            self.root / "resources.jsonl",
            {
                "ts_mono_ns": mono,
                "ts_utc_ns": time.time_ns(),
                "owned_pids": sorted(active),
                "cpu_percent_one_core": cpu_percent,
                "rss_bytes": rss_bytes,
                "disk_read_bytes": read_bytes,
                "disk_write_bytes": write_bytes,
                "utc_mapping_drift_ms": (time.time_ns() - self.origin.utc_ns(mono)) / 1e6,
                "scope": self.scope,
            },
        )
        self.previous, self.last = (now, cpu_s), now
