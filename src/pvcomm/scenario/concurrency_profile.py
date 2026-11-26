"""Finite resource comparison using independent, unchanged serial batch runners."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pvcomm.clock import ClockOrigin
from pvcomm.config.batch import BatchConfig
from pvcomm.config.scenes import canonical_hash
from pvcomm.config.workbench import ExperimentRequest, requested_matrix
from pvcomm.scenario.batch import (
    code_version,
    duration_s,
    projected_retained_bytes,
    save_json,
    validate_resume,
)
from pvcomm.scenario.batch_resources import active_storage, descendants
from pvcomm.scenario.resources import ResourceSampler


def profile_templates() -> tuple[BatchConfig, BatchConfig]:
    """Freeze conditions before either concurrency setting runs; no tuning."""
    return (
        requested_matrix(
            ExperimentRequest(template="N01", site_count=30, seed=920001, variant=0),
            "profile_normal_0",
        ),
        requested_matrix(
            ExperimentRequest(template="N03", site_count=30, seed=920002, variant=1),
            "profile_normal_1",
        ),
    )


def queue_result(root: Path, matrix: Path, sha: str) -> dict[str, Any]:
    """Verify retained bytes and identities, not just a successful subprocess exit."""
    config = BatchConfig.model_validate_json(matrix.read_bytes())
    if BatchConfig.model_validate_json((root / "queue/matrix.json").read_bytes()) != config:
        raise ValueError("profile queue matrix changed")
    state = json.loads((root / "queue/manifest.json").read_bytes())
    measurements = json.loads((root / "measurements/summary.json").read_bytes())
    if state["code_version"] != sha or state["matrix_sha256"] != canonical_hash(
        config.model_dump(mode="json")
    ):
        raise ValueError("profile queue source or matrix fingerprint changed")
    validate_resume(config, root / "queue", state)
    results = [a.get("result", {}) for t in state["tasks"] for a in t["attempts"]]
    valid = (
        state["status"] == "completed"
        and measurements["sampling_complete"] is True
        and measurements["samples"] > 0
        and len(results) == 1
        and results[0].get("accepted") is True
        and isinstance(results[0].get("run_id"), str)
        and all(
            t["status"] == "completed"
            and all(a["status"] == "completed" and a["artifacts"] for a in t["attempts"])
            for t in state["tasks"]
        )
    )
    return dict(
        valid=bool(valid),
        status=state["status"],
        coverage=state["coverage"],
        resources=measurements,
        run_ids=[r["run_id"] for r in results if "run_id" in r],
        matrix_sha256=state["matrix_sha256"],
        artifact_checksums_verified=True,
    )


def execute_profile_queue(root: Path, matrix: Path, source: Path, sha: str) -> dict[str, Any]:
    command = [
        "/usr/bin/time",
        "-v",
        "-o",
        str(root / "process.resources"),
        sys.executable,
        "scripts/run_v04_batch.py",
        "--matrix",
        str(matrix),
        "--output",
        str(root / "queue"),
        "--measurements",
        str(root / "measurements"),
    ]
    with (root / "execution.log").open("xb") as log:
        result = subprocess.run(
            command,
            cwd=source,
            env=dict(os.environ, PYTHONPATH="src"),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        record = queue_result(root, matrix, sha)
    except Exception as exc:
        record = dict(valid=False, error=f"{type(exc).__name__}: {exc}")
    return dict(exit_code=result.returncode, **record)


def lab_namespaces() -> list[str]:
    """Read-only inventory; existing unrelated namespaces are never removed."""
    output = subprocess.check_output(["ip", "netns", "list"], text=True)
    return sorted(
        line.split()[0]
        for line in output.splitlines()
        if line.split() and line.split()[0].startswith("pvcl-")
    )


def storage_reservation(templates: tuple[BatchConfig, ...]) -> int:
    retained = 2 * sum(
        projected_retained_bytes(t, sum(map(duration_s, t.tasks))) for t in templates
    )
    temporary = sum(t.retention.measured_parse_temporary_bytes for t in templates)
    return retained + temporary + max(t.minimum_free_bytes for t in templates)


def sample_storage(paths: list[tuple[Path, Path]]) -> dict[str, int]:
    """Aggregate simultaneous storage; separate queues' maxima cannot be added."""
    totals: dict[str, int] = {}
    for root, _matrix in paths:
        path = root / "queue/summary.json"
        state = json.loads(path.read_bytes()) if path.exists() else {}
        for key, value in active_storage(root / "queue", state).items():
            totals[key] = totals.get(key, 0) + value
    return totals


def profile_concurrency(output: Path) -> dict[str, Any]:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        raise RuntimeError("concurrency measurement requires authorized Linux lab")
    sha = code_version()
    # No network modifications here: just refuse overlap with another PVComm lab.
    if lab_namespaces():
        raise RuntimeError("finish the active PVComm experiment before throughput profiling")
    source = Path(__file__).resolve().parents[3]
    output = output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("profile output must be separate from the frozen source directory")
    if output.exists():
        raise FileExistsError(output)
    templates = profile_templates()
    # Conservative whole-profile retention plus two parser temporary lower bounds.
    # This is a reservation, not a claim to have measured the actual peak yet.
    required = storage_reservation(templates)
    ancestor = output.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < required:
        raise RuntimeError(
            "insufficient storage for both profile settings and temporary reservation"
        )
    output.mkdir(parents=True, exist_ok=False)
    save_json(
        output / "plan.json",
        dict(
            version="v04-concurrency-profile-v1",
            code_version=sha,
            settings=[1, 2],
            templates=[t.model_dump(mode="json") for t in templates],
            reserved_bytes=required,
            production_queue_maximum_concurrency=1,
            reused_serial_queues=True,
            no_formal_720h_generation=True,
        ),
    )
    settings: list[dict[str, Any]] = []
    for workers in (1, 2):
        directory = output / f"concurrency_{workers}"
        directory.mkdir()
        paths = []
        for i, template in enumerate(templates):
            root = directory / f"task_{i}"
            root.mkdir()
            matrix = root / "matrix.json"
            save_json(matrix, template.model_dump(mode="json"))
            paths.append((root, matrix))
        sampler = ResourceSampler(
            directory, ClockOrigin.capture(), scope="profile_queues_and_descendants"
        )
        errors = []
        storage_peak: dict[str, int] = {}
        samples = 0
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(execute_profile_queue, r, m, source, sha) for r, m in paths]
            while not all(future.done() for future in futures):
                try:
                    sampler.sample(descendants(os.getpid()))
                    storage = sample_storage(paths)
                    with (directory / "storage.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(dict(ts_mono_ns=time.monotonic_ns(), **storage)))
                        stream.write("\n")
                    for key, value in storage.items():
                        storage_peak[key] = max(storage_peak.get(key, 0), value)
                    samples += 1
                except (OSError, ValueError) as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                time.sleep(1)
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(dict(valid=False, error=f"{type(exc).__name__}: {exc}"))
        elapsed = time.monotonic() - started
        remaining = lab_namespaces()
        if remaining:
            errors.append(f"lab resources not cleaned: {remaining}")
        run_ids = [identity for r in results for identity in r.get("run_ids", [])]
        if len(run_ids) != 2 or len(set(run_ids)) != 2:
            errors.append("two distinct independent run identities are required")
        record = dict(
            concurrency=workers,
            elapsed_s=elapsed,
            queues=results,
            sampling_errors=errors,
            samples_are_lower_bounds=True,
            samples=samples,
            simultaneous_storage_peak_bytes=storage_peak,
            valid=elapsed > 0
            and samples > 0
            and not errors
            and all(r.get("valid") and r.get("exit_code") == 0 for r in results),
        )
        save_json(directory / "summary.json", record)
        settings.append(record)
        if not record["valid"]:
            break  # Keep failures; do not promote an unvalidated setting.
    run_ids = [identity for s in settings for q in s["queues"] for identity in q.get("run_ids", [])]
    valid = len(settings) == 2 and all(s["valid"] for s in settings) and len(set(run_ids)) == 4
    report = dict(
        version="v04-concurrency-profile-v1",
        code_version=sha,
        settings=settings,
        evaluation_valid=valid,
        measured_speedup=settings[0]["elapsed_s"] / settings[1]["elapsed_s"] if valid else None,
        production_queue_maximum_concurrency=1,
        limitations=(
            "two fixed 30-site normal templates; not sustained 720-hour or anomaly throughput; "
            "one-second samples miss short peaks; summed RSS includes shared pages; "
            "CPU deltas are estimates affected by process exit"
        ),
    )
    save_json(output / "report.json", report)
    return report
