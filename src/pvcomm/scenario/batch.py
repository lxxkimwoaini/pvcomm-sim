"""Serial resumable acceptance queue; delegates all simulation to existing runners."""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import time
import traceback
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pvcomm.config.batch import BatchConfig, BatchJob, FaultJob, PairJob
from pvcomm.config.faults import instantiate_fault
from pvcomm.config.pairs import PairConfig
from pvcomm.config.scenes import canonical_hash, instantiate_scenario
from pvcomm.pipeline.labels import file_sha256

VERSION = "v04-batch-v1"


def duration_s(task: BatchJob) -> int:
    if isinstance(task, PairJob):
        return (
            task.pair.normal.simulation.protocol.duration_s
            + task.pair.changed.background.simulation.protocol.duration_s
        )
    if isinstance(task, FaultJob):
        return task.scenario.background.simulation.protocol.duration_s
    return task.scenario.simulation.protocol.duration_s


def projected_retained_bytes(config: BatchConfig, seconds: int) -> int:
    policy = config.retention
    return math.ceil(seconds / 3600 * policy.retained_bytes_per_hour * policy.capacity_margin)


def code_version() -> str:
    repository = Path(__file__).resolve().parents[3]
    if subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"]):
        raise ValueError("batch execution requires frozen clean source")
    return subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()


def save_json(path: Path, value: object) -> None:
    stage = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        stage.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        stage.replace(path)
    finally:
        stage.unlink(missing_ok=True)


def artifact_inventory(root: Path) -> list[dict[str, Any]]:
    """Bounded hashing, never reads a complete capture/Parquet into memory."""
    return [
        dict(
            path=path.relative_to(root).as_posix(),
            bytes=path.stat().st_size,
            sha256=file_sha256(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def validate_resume(config: BatchConfig, output: Path, state: dict[str, Any]) -> None:
    if len(state["tasks"]) != len(config.tasks):
        raise ValueError("retained task membership changed")
    for task, spec in zip(state["tasks"], config.tasks, strict=True):
        if (
            task["task_id"] != spec.task_id
            or task["kind"] != spec.kind
            or len(task["attempts"]) > spec.max_attempts
        ):
            raise ValueError("retained task identity or retry budget changed")
        for index, attempt in enumerate(task["attempts"], 1):
            expected = f"{spec.task_id}/attempt_{index}"
            if attempt["directory"] != expected:
                raise ValueError("unsafe retained attempt directory")
            directory = output / expected
            for artifact in attempt.get("artifacts", []):
                path = directory / artifact["path"]
                if (
                    not path.resolve().is_relative_to(directory.resolve())
                    or not path.is_file()
                    or path.stat().st_size != artifact["bytes"]
                    or file_sha256(path) != artifact["sha256"]
                ):
                    raise ValueError("retained artifact checksum changed")


def pair_counts(report: dict[str, Any]) -> dict[str, int]:
    return dict(
        attempted_pairs=1,
        valid_pairs=int(report["data_quality_passed"]),
        business_contrast_pairs=int(report["data_quality_passed"] and report["business_contrast"]),
        matched_pairs=int(report["hard_pair_selected"]),
    )


def execute_job(task: BatchJob, directory: Path, config: BatchConfig) -> dict[str, Any]:
    """One new attempt; real-time protocols are never resumed partway through."""
    if isinstance(task, PairJob):
        from pvcomm.qc.similarity import evaluate_pair_similarity
        from pvcomm.scenario.pairs import run_pair

        identity = f"pair_{uuid.uuid4().hex}"
        raw = task.pair.model_dump(mode="json")
        raw["pair_id"] = raw["normal"]["pair_id"] = raw["changed"]["background"]["pair_id"] = (
            identity
        )
        root = run_pair(PairConfig.model_validate(raw), directory)
        assert config.matching is not None
        report = evaluate_pair_similarity(root, config.matching.policy, config.matching.calibration)
        ledger = json.loads((root / "pair-manifest.json").read_bytes())
        return dict(
            root=root.relative_to(directory).as_posix(),
            accepted=bool(report["data_quality_passed"]),
            pair_type=task.pair.pair_type,
            counts=pair_counts(report),
            similarity_matched=report["matched"],
            business_contrast=report["business_contrast"],
            source_group_id=task.pair.normal.source_group_id,
            independent_runs=[
                dict(
                    run_id=r["run_id"],
                    fault_type=task.pair.changed.fault.fault_type
                    if r["member"] == "changed"
                    else None,
                )
                for r in ledger["members"]
            ],
        )
    from pvcomm.pipeline.iec104_parser import parse_run
    from pvcomm.pipeline.labels import label_run
    from pvcomm.qc.fault import qc_fault
    from pvcomm.qc.labels import qc_labels
    from pvcomm.qc.schedule import qc_schedule
    from pvcomm.scenario.isolated_runner import run_isolated
    from pvcomm.storage.parquet import export_parquet
    from pvcomm.storage.power_parquet import export_power_parquet

    identity = f"run_{uuid.uuid4().hex}"
    if isinstance(task, FaultJob):
        fault = instantiate_fault(task.scenario, identity)
        scene = fault.background
    else:
        fault = None
        scene = instantiate_scenario(task.scenario, identity)
    source = directory / "input.json"
    source.write_text((fault or scene).model_dump_json(indent=2) + "\n", encoding="utf-8")
    root = run_isolated(
        scene.simulation.protocol,
        source,
        directory,
        power=scene.simulation,
        scenario=scene,
        fault=fault,
    )
    parse_run(root)
    export_parquet(root)
    export_power_parquet(root)
    quality = qc_fault(root) if fault else qc_schedule(root)
    label_run(root)
    labels = qc_labels(root)
    accepted = quality["passed"] and labels["passed"] and labels["invalid_labels"] == 0
    if fault is None:
        accepted = (
            accepted and not labels["uncertain_labels"] and not labels["service_violation_stations"]
        )
    return dict(
        root=root.relative_to(directory).as_posix(),
        accepted=bool(accepted),
        run_id=identity,
        source_group_id=scene.source_group_id,
        fault_type=fault.fault.fault_type if fault else None,
        normal_scene=scene.normal_scene,
        uncertain_labels=labels["uncertain_labels"],
        service_violation_stations=labels["service_violation_stations"],
    )


def coverage(state: dict[str, Any]) -> dict[str, Any]:
    """Only independent attempts count; windows/stations and retries are not quotas."""
    result: dict[str, Any] = dict(
        tasks=len(state["tasks"]),
        completed_tasks=0,
        failed_tasks=0,
        attempts=0,
        independent_run_ids=[],
        valid_fault_runs={},
        valid_normal_runs=0,
        pairs={},
    )
    for task in state["tasks"]:
        result["completed_tasks"] += task["status"] == "completed"
        result["failed_tasks"] += task["status"] == "failed"
        result["attempts"] += len(task["attempts"])
        # First accepted attempt per task contributes to coverage, avoiding retry inflation.
        accepted = False
        for attempt in task["attempts"]:
            record = attempt.get("result", {})
            if task["kind"] == "pair":
                own = result["pairs"].setdefault(
                    task["pair_type"],
                    dict(
                        attempted_pairs=0, valid_pairs=0, business_contrast_pairs=0, matched_pairs=0
                    ),
                )
                own["attempted_pairs"] += 1
                for key, value in record.get("counts", {}).items():
                    if key != "attempted_pairs":
                        own[key] += value
            if not record.get("accepted") or accepted:
                continue
            accepted = True
            runs = record.get("independent_runs", [record] if "run_id" in record else [])
            for run in runs:
                result["independent_run_ids"].append(run["run_id"])
                if run.get("fault_type"):
                    kind = run["fault_type"]
                    result["valid_fault_runs"][kind] = result["valid_fault_runs"].get(kind, 0) + 1
                else:
                    result["valid_normal_runs"] += 1
    if len(set(result["independent_run_ids"])) != len(result["independent_run_ids"]):
        raise ValueError("duplicate independent run identity in coverage")
    return result


def run_batch(config: BatchConfig, output: Path, *, resume: bool = False) -> dict[str, Any]:
    if os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        raise RuntimeError("batch requires explicitly authorized isolated laboratory execution")
    sha = code_version()
    projected = projected_retained_bytes(
        config, sum(duration_s(t) * t.max_attempts for t in config.tasks)
    )
    if projected > config.maximum_output_bytes:
        raise ValueError("matrix projected retained storage exceeds acceptance output budget")
    fingerprint = canonical_hash(config.model_dump(mode="json"))
    output.mkdir(parents=True, exist_ok=resume)
    # A separate SQLite write lock survives across manifest commits and is released
    # by the OS on a crash. It exposes no arbitrary commands and permits status reads.
    with closing(sqlite3.connect(output / ".execution-lock.sqlite", timeout=0)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        path = output / "manifest.json"
        if resume:
            state: dict[str, Any] = json.loads(path.read_bytes())
            if state["matrix_sha256"] != fingerprint or state["code_version"] != sha:
                raise ValueError("resume requires the same frozen matrix and code version")
            if json.loads((output / "matrix.json").read_bytes()) != config.model_dump(mode="json"):
                raise ValueError("retained matrix content changed")
            validate_resume(config, output, state)
        else:
            (output / "matrix.json").write_text(
                config.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            state = dict(
                batch_version=VERSION,
                matrix_id=config.matrix_id,
                matrix_sha256=fingerprint,
                code_version=sha,
                scope="Oracle_experiment_management_never_M2_features",
                created_at=datetime.now(UTC).isoformat(),
                storage_plan=dict(
                    projected_retained_bytes=projected,
                    temporary_lower_bound_bytes=config.retention.measured_parse_temporary_bytes,
                    temporary_peak_measured=False,
                    concurrency_throughput_validated=False,
                    basis="V0.3 measurement and configured margin; not measured batch peak",
                ),
                status="queued",
                tasks=[
                    dict(
                        task_id=t.task_id,
                        kind=t.kind,
                        status="queued",
                        attempts=[],
                        **(dict(pair_type=t.pair.pair_type) if isinstance(t, PairJob) else {}),
                    )
                    for t in config.tasks
                ],
            )

        def save() -> None:
            state["coverage"] = coverage(state)
            state["updated_at"] = datetime.now(UTC).isoformat()
            save_json(path, state)

        # A crashed real-time run remains evidence. Only a fresh independent attempt
        # can be scheduled, and it consumes the finite retry budget.
        for task in state["tasks"]:
            for previous_attempt in task["attempts"]:
                if previous_attempt["status"] == "running":
                    previous_attempt.update(
                        status="interrupted",
                        error="previous executor stopped before recording completion",
                    )
            if task["status"] == "running":
                task["status"] = "queued"
        state["status"] = "running"
        state.pop("error", None)
        save()
        try:
            for specification, task in zip(config.tasks, state["tasks"], strict=True):
                if task["status"] == "completed":
                    continue
                while len(task["attempts"]) < specification.max_attempts:
                    used = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
                    free = shutil.disk_usage(output).free
                    next_bytes = projected_retained_bytes(config, duration_s(specification))
                    if (
                        free
                        < config.minimum_free_bytes
                        + next_bytes
                        + config.retention.measured_parse_temporary_bytes
                        or used + next_bytes > config.maximum_output_bytes
                    ):
                        raise RuntimeError(f"storage budget blocked: free={free}, used={used}")
                    directory = output / task["task_id"] / f"attempt_{len(task['attempts']) + 1}"
                    directory.mkdir(parents=True, exist_ok=False)
                    attempt: dict[str, Any] = dict(
                        status="running",
                        directory=directory.relative_to(output).as_posix(),
                        started_at=datetime.now(UTC).isoformat(),
                    )
                    task["attempts"].append(attempt)
                    task["status"] = "running"
                    save()
                    began = time.monotonic()
                    try:
                        attempt["result"] = execute_job(specification, directory, config)
                        attempt["status"] = (
                            "completed" if attempt["result"]["accepted"] else "qc_failed"
                        )
                    except BaseException as exc:
                        attempt.update(
                            status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        (directory / "failure.txt").write_text(
                            traceback.format_exc(), encoding="utf-8"
                        )
                        if not isinstance(exc, Exception):
                            raise
                    finally:
                        attempt["elapsed_s"] = time.monotonic() - began
                        attempt["artifacts"] = artifact_inventory(directory)
                        attempt["retained_bytes"] = sum(a["bytes"] for a in attempt["artifacts"])
                        save()
                    if attempt["status"] == "completed":
                        task["status"] = "completed"
                        break
                if task["status"] != "completed":
                    task["status"] = "failed"
                save()
            state["status"] = (
                "completed"
                if all(t["status"] == "completed" for t in state["tasks"])
                else "completed_with_failures"
            )
        except BaseException as exc:
            state.update(
                status="cancelled" if isinstance(exc, KeyboardInterrupt) else "blocked",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            save()
    return state
