"""Offline measurements from retained evidence; never detection features or labels."""

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def records(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "mean": sum(ordered) / len(ordered) if ordered else None,
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1] if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


def performance_report(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    config = json.loads((root / "power_config.json").read_text(encoding="utf-8"))
    scene = manifest["scene_clock"]
    start = scene["start_mono_ns"]
    duration = config["protocol"]["duration_s"]
    resources = list(records([root / "resources.jsonl"]))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in resources:
        elapsed = (row["ts_mono_ns"] - start) / 1e9
        if 0 <= elapsed < duration:
            grouped["off" if elapsed < 1200 else "live" if elapsed < 2400 else "replay"].append(row)
    phases = {}
    for name, rows in grouped.items():
        phases[name] = {
            field: distribution(r[field] for r in rows if r[field] is not None)
            for field in ("cpu_percent_one_core", "rss_bytes", "utc_mapping_drift_ms")
        }
        phases[name]["disk_read_bytes_last"] = rows[-1]["disk_read_bytes"]
        phases[name]["disk_write_bytes_last"] = rows[-1]["disk_write_bytes"]
    steps: dict[str, list[float]] = defaultdict(list)
    previous: dict[str, int] = {}
    for row in records(sorted((root / "truth").glob("electrical_state*.jsonl"))):
        if row.get("source") != "pvlib_pvwatts_inverter_v02":
            continue
        site, stamp = row["station_id"], row["model_mono_ns"]
        if site in previous and start <= previous[site] < stamp:
            steps[site].append((stamp - previous[site]) / 1e6)
        previous[site] = stamp
    solves: dict[int, float] = {}
    for row in records(sorted((root / "truth").glob("terminal_events*.jsonl"))):
        if row["event_type"] == "terminal_feeder_diagnostics":
            solves[row["solved_mono_ns"]] = row["solve_duration_ms"]
    sizes: dict[str, int] = defaultdict(int)
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root)
            category = (
                "pcap"
                if path.suffix == ".pcap"
                else ("parquet" if path.suffix == ".parquet" else relative.parts[0])
            )
            sizes[category] += path.stat().st_size
    total = sum(sizes.values())
    return {
        "scope": "infrastructure_and_Oracle_diagnostics_not_M2",
        "run_id": manifest["run_id"],
        "code_version": manifest["code_version"],
        "status": manifest["status"],
        "duration_s": duration,
        "stations": len(config["protocol"]["stations"]),
        "regions": len(config["regions"]),
        "phase_assumption": "scene [0,1200) off; [1200,2400) live; [2400,3600) replay; "
        "verify actual Web workload markers separately, short runs only cover off",
        "simulation_processes_by_phase": phases,
        "electrical_inter_update_ms": {k: distribution(v) for k, v in steps.items()},
        "electrical_interval_scope": "physical samples including command-triggered advances",
        "powerflow_solve_ms": distribution(solves.values()),
        "capture_drops": {
            c["path"]: c["statistics"]["packets_dropped_by_kernel"] for c in manifest["captures"]
        },
        "storage_bytes_by_category": dict(sizes),
        "storage_total_bytes": total,
        "estimated_720h_bytes_with_1_5_headroom": total / duration * 3600 * 720 * 1.5,
        "estimate_limitations": "linear estimate from this measured workload; backups separate; "
        "720h serial real-time; ideal eight instances 90h is unverified capacity, not a promise",
        "cleanup_errors": manifest["cleanup_errors"],
    }
