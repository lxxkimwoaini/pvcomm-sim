"""Offline measurements from retained evidence; never detection features or labels."""

import json
import math
import sys
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


def web_performance_report(root: Path) -> dict[str, Any]:
    """Summarize the explicit real-browser workload, retaining missing evidence."""
    workload = json.loads((root / "workload.json").read_text(encoding="utf-8"))
    origin, phase_s = workload["origin_utc_ms"], workload["phase_seconds"]
    phases = list(records([root / "phases.jsonl"]))
    requests = list(records([root / "requests.jsonl"]))
    displays = list(records([root / "display.jsonl"]))
    errors = list(records([root / "errors.jsonl"])) if (root / "errors.jsonl").exists() else []
    api_root = workload["api_measurements_linux"]
    if sys.platform == "win32":
        api_root = "\\\\wsl.localhost\\Ubuntu" + api_root.replace("/", "\\")
    api_rows = list(records([Path(api_root) / "resources.jsonl"]))
    windows_rows = list(records([root / "windows-resources.jsonl"]))
    browser_rows = list(records([root / "browser.jsonl"]))
    delay = [
        sample["master_available_to_paint_ms"]
        for row in displays
        if row["mode"] == "live" and phase_s <= row["elapsed_s"] < 2 * phase_s
        for sample in row["newly_displayed_power"]
    ]
    delays = distribution(delay)
    summaries = {}
    for name, index in (("live", 1), ("replay", 2)):
        subset = [r for r in requests if r["phase"] == name]
        api = [
            r
            for r in api_rows
            if index * phase_s <= (r["ts_utc_ns"] / 1e6 - origin) / 1000 < (index + 1) * phase_s
        ]
        windows = [
            r
            for r in windows_rows
            if index * phase_s <= (r["ts_utc_ms"] - origin) / 1000 < (index + 1) * phase_s
        ]
        heap = [
            metric["value"]
            for r in browser_rows
            if index * phase_s <= r["elapsed_s"] < (index + 1) * phase_s
            for metric in r["metrics"]
            if metric["name"] == "JSHeapUsedSize"
        ]
        summaries[name] = {
            "requests": len(subset),
            "request_span_s": (
                max(r["elapsed_s"] for r in subset) - min(r["elapsed_s"] for r in subset)
                if subset
                else 0
            ),
            "non_200_responses": sum(r["status"] != 200 for r in subset),
            "payload_bytes": sum(r["bytes"] for r in subset),
            "request_end_to_end_ms": distribution(r["latency_ms"] for r in subset),
            "server_ms": distribution(r["server_ms"] for r in subset),
            "api_cpu_percent_one_core": distribution(
                r["cpu_percent_one_core"] for r in api if r["cpu_percent_one_core"] is not None
            ),
            "api_rss_bytes": distribution(r["rss_bytes"] for r in api),
            "api_disk_read_bytes_first_last": [
                r["disk_read_bytes"] for r in api[:: max(1, len(api) - 1)]
            ],
            "api_disk_write_bytes_first_last": [
                r["disk_write_bytes"] for r in api[:: max(1, len(api) - 1)]
            ],
            "windows_cpu_percent_one_core": distribution(
                r["cpu_percent_one_core"] for r in windows if r["cpu_percent_one_core"] is not None
            ),
            "windows_working_set_bytes_sum": distribution(
                r["working_set_bytes_sum"] for r in windows
            ),
            "browser_js_heap_bytes": distribution(heap),
        }
    passed = (
        not workload["standalone"]
        and not workload["client_code_dirty"]
        and phase_s == 1200
        and {r["phase"] for r in phases} >= {"off", "live", "replay", "completed"}
        and bool(delay)
        and min(delay) >= 0
        and delays["p95"] is not None
        and delays["p95"] <= 2000
        and not errors
        and all(
            s["requests"] > 0
            and s["request_span_s"] >= phase_s * 0.9
            and s["non_200_responses"] == 0
            and s["api_rss_bytes"]["samples"] > 0
            and s["windows_working_set_bytes_sum"]["samples"] > 0
            for s in summaries.values()
        )
    )
    return {
        "workload": workload,
        "phase_markers": phases,
        "by_phase": summaries,
        "live_new_power_available_to_approximate_paint_ms": delays,
        "errors": errors,
        "passed": passed,
        "limitations": (
            "M2 master availability to approximate browser paint, excludes initial snapshots; "
            "replay ages excluded. Windows process-tree memory can double count shared pages; "
            "Linux API measured separately. Sequential phases are engineering diagnostics, "
            "not a randomized causal performance experiment."
        ),
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
