"""Recover only the outer coordinator without changing frozen workers or evidence."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import runpy
import sys
import time
from pathlib import Path
from typing import Any


def process_sample(
    pids: list[int], *, proc: Path, clock_ticks: int, page_size: int
) -> dict[str, Any]:
    cpu_s, rss_bytes, read_bytes, write_bytes = 0.0, 0, 0, 0
    active: list[int] = []
    errors: list[dict[str, Any]] = []
    for pid in sorted(set(pids)):
        try:
            stat = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError, PermissionError) as exc:
            errors.append(dict(pid=pid, field="stat", error=type(exc).__name__))
            continue
        cpu_s += (int(stat[11]) + int(stat[12])) / clock_ticks
        rss_bytes += int(stat[21]) * page_size
        active.append(pid)
        try:
            io = dict(
                line.split(": ", 1) for line in (proc / str(pid) / "io").read_text().splitlines()
            )
            read_bytes += int(io["read_bytes"])
            write_bytes += int(io["write_bytes"])
        except (FileNotFoundError, ProcessLookupError, PermissionError) as exc:
            errors.append(dict(pid=pid, field="io", error=type(exc).__name__))
    return dict(
        owned_pids=active,
        cpu_s=cpu_s,
        rss_bytes=rss_bytes,
        disk_read_bytes=None if errors else read_bytes,
        disk_write_bytes=None if errors else write_bytes,
        readable_process_disk_read_bytes=read_bytes,
        readable_process_disk_write_bytes=write_bytes,
        sample_errors=errors,
        process_stat_complete=not any(e["field"] == "stat" for e in errors),
        process_io_complete=not errors,
    )


def install_sampler(plan: Path, driver: dict[str, Any]) -> None:
    from pvcomm.scenario.batch_resources import descendants
    from pvcomm.scenario.resources import ResourceSampler

    class RecoverySampler(ResourceSampler):
        def sample(self, pids: list[int]) -> None:
            if sys.platform == "win32":
                raise RuntimeError("coordinator resource sampling requires Linux")
            now = time.monotonic()
            if now - self.last < 1:
                return
            # Workers from the former coordinator were reparented to PID 1.
            # Include them only when their recorded /proc start identity still matches.
            index = json.loads((plan / "parallel-index.json").read_bytes())
            owned = [os.getpid(), *pids]
            for worker in index["workers"].values():
                if worker["status"] == "running" and driver["identity_alive"](worker):
                    owned.extend([worker["pid"], *descendants(worker["pid"])])
            row = process_sample(
                owned,
                proc=Path("/proc"),
                clock_ticks=os.sysconf("SC_CLK_TCK"),
                page_size=os.sysconf("SC_PAGE_SIZE"),
            )
            cpu_s = row.pop("cpu_s")
            row["cpu_percent_one_core"] = (
                None
                if self.previous is None
                else max(0.0, (cpu_s - self.previous[1]) / (now - self.previous[0]) * 100)
            )
            mono = time.monotonic_ns()
            row.update(
                ts_mono_ns=mono,
                ts_utc_ns=time.time_ns(),
                utc_mapping_drift_ms=(time.time_ns() - self.origin.utc_ns(mono)) / 1e6,
                scope=self.scope,
                sampled_peak_is_lower_bound=True,
                cpu_counter_excludes_exited_processes=True,
            )
            with (self.root / "resources.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            self.previous, self.last = (now, cpu_s), now

    # This assignment affects only the new coordinator process, never active workers.
    resource_module: Any = importlib.import_module("pvcomm.scenario.resources")
    resource_module.ResourceSampler = RecoverySampler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads((args.plan / "protocol.json").read_bytes())
    driver = runpy.run_path(protocol["driver"])
    driver["verify"](args.plan)
    install_sampler(args.plan, driver)
    driver["control"](args.plan, resume=True)


if __name__ == "__main__":
    main()
