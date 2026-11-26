"""Filesystem request spool; a separately authorized local worker executes batches."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pvcomm.config.batch import BatchConfig, MatchingInputs
from pvcomm.config.scenes import canonical_hash
from pvcomm.config.workbench import ExperimentRequest, requested_matrix
from pvcomm.scenario.batch import run_batch, save_json

SCOPE: Literal["Oracle_experiment_management_never_M2_features"] = (
    "Oracle_experiment_management_never_M2_features"
)


def safe_file(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("experiment path escapes configured root")
    return path


def read_json(root: Path, path: Path, maximum_bytes: int = 16 * 1024**2) -> dict[str, Any]:
    path = safe_file(root, path)
    with path.open("rb") as stream:
        data = stream.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise ValueError("experiment metadata exceeds read budget")
    result: dict[str, Any] = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("experiment metadata must be an object")
    return result


def submit_request(
    root: Path, request: ExperimentRequest, matching: MatchingInputs | None = None
) -> dict[str, Any]:
    identity = f"experiment_{uuid.uuid4().hex}"
    matrix = requested_matrix(request, identity, matching)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / identity
    directory.mkdir(exist_ok=False)
    # request.json is the last, atomic commit marker; partial directories are ignored.
    save_json(directory / "matrix.json", matrix.model_dump(mode="json"))
    record = dict(
        request_version="v04-experiment-request-v1",
        scope=SCOPE,
        experiment_id=identity,
        created_at=datetime.now(UTC).isoformat(),
        request=request.model_dump(mode="json"),
        matrix_sha256=canonical_hash(matrix.model_dump(mode="json")),
        status="queued",
    )
    save_json(directory / "request.json", record)
    return record


def execute_requests(root: Path, *, resume: bool = False, maximum_jobs: int = 1) -> list[str]:
    """Hold one spool-wide lock, delegate the entire experiment to run_batch."""
    if os.environ.get("PVCOMM_ALLOW_LAB") != "1" or sys.platform != "linux":
        raise RuntimeError("experiment worker requires authorized Linux lab execution")
    if not 1 <= maximum_jobs <= 1000:
        raise ValueError("invalid finite worker budget")
    root = root.resolve(strict=True)
    completed = []
    with closing(sqlite3.connect(root / ".worker-lock.sqlite", timeout=0)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        requests = []
        for directory in root.glob("experiment_*"):
            if directory.is_symlink() or not (directory / "request.json").is_file():
                continue
            request = read_json(root, directory / "request.json")
            requests.append((datetime.fromisoformat(request["created_at"]), directory, request))
        for _created, directory, request in sorted(requests, key=lambda row: (row[0], row[1].name)):
            if request["experiment_id"] != directory.name or request["scope"] != SCOPE:
                raise ValueError("request identity or observation scope changed")
            state_path = directory / "worker.json"
            previous = read_json(root, state_path) if state_path.exists() else {}
            if previous.get("status") in ("completed", "completed_with_failures", "failed"):
                continue
            if previous.get("status") == "running" and not resume:
                continue
            matrix = BatchConfig.model_validate(read_json(root, directory / "matrix.json"))
            if canonical_hash(matrix.model_dump(mode="json")) != request["matrix_sha256"]:
                raise ValueError("submitted matrix checksum changed")
            output = safe_file(root, directory / "queue")
            save_json(state_path, dict(status="running", started_at=datetime.now(UTC).isoformat()))
            try:
                result = run_batch(matrix, output, resume=output.exists() and resume)
                save_json(
                    state_path, dict(status=result["status"], updated_at=result["updated_at"])
                )
            except Exception as exc:
                save_json(state_path, dict(status="failed", error=f"{type(exc).__name__}: {exc}"))
                raise
            completed.append(directory.name)
            if len(completed) == maximum_jobs:
                break
    return completed
