"""Read-only sampling around the existing serial runner, scoped to its descendants."""

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from pvcomm.clock import ClockOrigin
from pvcomm.power.runtime import append_snapshot
from pvcomm.scenario.resources import ResourceSampler


def descendants(pid: int, proc: Path = Path("/proc")) -> list[int]:
    found, pending = set(), [pid]
    while pending:
        parent = pending.pop()
        for child_file in (proc / str(parent) / "task").glob("*/children"):
            try:
                children = [int(p) for p in child_file.read_text().split()]
            except FileNotFoundError:
                continue
            for child in children:
                if child != pid and child not in found:
                    found.add(child)
                    pending.append(child)
    return sorted(found)


def active_storage(root: Path, state: dict[str, Any]) -> dict[str, int]:
    result = dict(
        finalized_retained_bytes=0,
        active_bytes=0,
        parse_temporary_bytes=0,
        pdml_bytes=0,
        label_index_bytes=0,
    )
    for task in state.get("tasks", []):
        for attempt in task["attempts"]:
            if attempt["status"] != "running":
                result["finalized_retained_bytes"] += attempt.get("retained_bytes", 0)
                continue
            directory = root / attempt["directory"]
            if not directory.resolve().is_relative_to(root.resolve()) or directory.is_symlink():
                raise ValueError("unsafe active attempt path")
            for path in directory.rglob("*"):
                try:
                    if not path.is_file() or path.is_symlink():
                        continue
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue  # Atomic parse/index commit can race a sample.
                result["active_bytes"] += size
                parts = path.relative_to(directory).parts
                if any(part.startswith(".parse-") for part in parts):
                    result["parse_temporary_bytes"] += size
                if path.suffix == ".pdml":
                    result["pdml_bytes"] += size
                if any(part.startswith(".label-index-") for part in parts):
                    result["label_index_bytes"] += size
    return result


class BatchResources:
    def __init__(self, output: Path, measurement: Path) -> None:
        if os.name != "posix" or not Path("/proc").is_dir():
            raise RuntimeError("batch resource sampling requires Linux /proc")
        self.output, self.measurement = output.resolve(), measurement.resolve()
        if self.output == self.measurement or self.output in self.measurement.parents:
            raise ValueError("measurements must be outside queue evidence")
        measurement.mkdir(parents=True, exist_ok=False)
        self.origin = ClockOrigin.capture()
        self.sampler = ResourceSampler(
            measurement, self.origin, scope="serial_queue_and_descendants"
        )
        self.stop = threading.Event()
        self.errors: list[str] = []
        self.peak: dict[str, int] = {}
        self.samples = 0
        self.thread = threading.Thread(target=self.run, name="batch-resource-sampler", daemon=True)

    def sample(self) -> None:
        self.sampler.sample(descendants(os.getpid()))
        # New queues publish an atomic compact projection. Retain the legacy
        # fallback for already frozen runs, without repeatedly decoding artifact hashes.
        projection = self.output / "summary.json"
        path = projection if projection.exists() else self.output / "manifest.json"
        state = json.loads(path.read_bytes()) if path.exists() else {}
        storage = active_storage(self.output, state)
        storage["free_bytes"] = shutil.disk_usage(self.measurement).free
        for field, value in storage.items():
            if field != "free_bytes":
                self.peak[field] = max(self.peak.get(field, 0), value)
        append_snapshot(
            self.measurement / "storage.jsonl",
            {
                "ts_mono_ns": time.monotonic_ns(),
                "ts_utc_ns": time.time_ns(),
                **storage,
            },
        )
        self.samples += 1

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self.sample()
                self.stop.wait(1.0)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")

    def __enter__(self) -> "BatchResources":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.errors.append("sampler did not stop within 10 seconds")
        else:
            try:
                self.sample()
            except Exception as exc:
                self.errors.append(f"final sample: {type(exc).__name__}: {exc}")
        record = dict(
            version="v04-batch-resource-v1",
            samples=self.samples,
            sample_period_s=1,
            observed_peak_bytes=self.peak,
            errors=self.errors,
            sampling_complete=not self.errors,
            sampled_peak_is_lower_bound=True,
            scope="current queue and descendants; no other experiments or Web",
        )
        (self.measurement / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
