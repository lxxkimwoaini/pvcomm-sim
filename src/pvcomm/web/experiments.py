"""Explicit Oracle management projection, separate from the M2 run repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from pvcomm.config.batch import MatchingInputs
from pvcomm.config.models import Contract
from pvcomm.config.normal_scenes import SCENE_CODES
from pvcomm.config.workbench import FAULT_CODES, PAIR_CODES, ExperimentRequest
from pvcomm.scenario.requests import SCOPE, read_json, safe_file, submit_request


class ExperimentView(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    experiment_id: str
    status: str
    request: dict[str, Any]
    queue: dict[str, Any] | None = None
    error: str | None = None


class ExperimentList(Contract):
    enabled: bool
    experiments: list[ExperimentView]
    total: int


class TemplateCatalog(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    normal: tuple[str, ...] = SCENE_CODES
    fault: tuple[str, ...] = FAULT_CODES
    pairs: tuple[str, ...] = PAIR_CODES
    strengths: dict[str, tuple[int, ...]]
    site_counts: tuple[int, ...] = (3, 10, 30)
    submission_enabled: bool
    pair_submission_enabled: bool
    execution: Literal["separate_authorized_linux_worker"] = "separate_authorized_linux_worker"


class RunQuality(Contract):
    run_key: str
    run_id: str
    status: str
    cleanup_errors: list[str]
    code_version: str | None
    reports: list[dict[str, Any]]


class ExperimentQuality(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    runs: list[RunQuality]


class LabelPage(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    labels: list[dict[str, Any]]
    total: int


def opaque_key(path: Path, root: Path) -> str:
    return hashlib.sha256(path.relative_to(root).as_posix().encode()).hexdigest()[:24]


class ExperimentRepository:
    def __init__(self, root: Path | None, matching: MatchingInputs | None = None) -> None:
        self.root = root.resolve() if root else None
        self.matching = matching

    def directories(self) -> dict[str, Path]:
        if self.root is None or not self.root.is_dir():
            return {}
        return {
            p.name: p
            for p in self.root.glob("experiment_*")
            if p.is_dir() and not p.is_symlink() and (p / "request.json").is_file()
        }

    def directory(self, identity: str) -> Path:
        if identity not in self.directories():
            raise FileNotFoundError("unknown experiment")
        return self.directories()[identity]

    def ordered_identities(self) -> list[str]:
        if self.root is None:
            return []
        directories = self.directories()
        created = {
            identity: read_json(self.root, directory / "request.json")["created_at"]
            for identity, directory in directories.items()
        }
        return sorted(created, key=lambda i: (created[i], i), reverse=True)

    def view(self, identity: str) -> ExperimentView:
        directory = self.directory(identity)
        assert self.root is not None
        request = read_json(self.root, directory / "request.json")
        if request["scope"] != SCOPE or request["experiment_id"] != identity:
            raise ValueError("request identity or scope changed")
        worker = (
            read_json(self.root, directory / "worker.json")
            if (directory / "worker.json").exists()
            else {}
        )
        # Deliberately never fall back to the full artifact manifest during polling.
        projection = directory / "queue/summary.json"
        queue = read_json(self.root, projection) if projection.exists() else None
        return ExperimentView(
            experiment_id=identity,
            status=queue["status"] if queue else worker.get("status", "queued"),
            request=request["request"],
            queue=queue,
            error=worker.get("error"),
        )

    def run_directories(self, identity: str) -> dict[str, Path]:
        root = self.directory(identity) / "queue"
        result = {}
        # Enumerate only run roots at the runner's two known depths, not all evidence files.
        for pattern in ("*/attempt_*/run_*", "*/attempt_*/pair_*/run_*"):
            for p in root.glob(pattern):
                if (
                    p.is_dir()
                    and p.resolve().is_relative_to(root.resolve())
                    and (p / "manifest.json").is_file()
                ):
                    result[opaque_key(p, root)] = p
        return result

    def diagnostics(self, identity: str) -> ExperimentQuality:
        assert self.root is not None
        runs = []
        for key, directory in self.run_directories(identity).items():
            manifest = read_json(self.root, directory / "manifest.json")
            reports = []
            for path in sorted((directory / "qc").glob("*/report.json")):
                report = read_json(self.root, path)
                # Physical snapshot arrays are large; the original report remains on disk.
                reports.append(
                    dict(
                        version=path.parent.name,
                        passed=report.get("passed"),
                        failed_checks=report.get("failed_checks", []),
                        invalid_labels=report.get("invalid_labels"),
                        uncertain_labels=report.get("uncertain_labels"),
                        service_violation_stations=report.get("service_violation_stations"),
                    )
                )
            runs.append(
                RunQuality(
                    run_key=key,
                    run_id=manifest["run_id"],
                    status=manifest["status"],
                    cleanup_errors=manifest["cleanup_errors"],
                    code_version=manifest.get("code_version"),
                    reports=reports,
                )
            )
        return ExperimentQuality(runs=runs)

    def labels(self, identity: str, run_key: str, offset: int, limit: int) -> LabelPage:
        runs = self.run_directories(identity)
        if run_key not in runs:
            raise FileNotFoundError("unknown run")
        assert self.root is not None
        path = safe_file(self.root, runs[run_key] / "labels/v04-label-v3/event_labels.jsonl")
        labels = []
        total = 0
        with path.open("rb") as stream:
            while line := stream.readline(4 * 1024**2 + 1):
                if len(line) > 4 * 1024**2:
                    raise ValueError("label record exceeds read budget")
                if offset <= total < offset + limit:
                    labels.append(json.loads(line))
                total += 1

        def precise(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: str(v)
                    if isinstance(v, int) and (k.endswith("_ns") or k == "available_at")
                    else precise(v)
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [precise(v) for v in value]
            return value

        return LabelPage(labels=precise(labels), total=total)


def experiment_router(repository: ExperimentRepository) -> APIRouter:
    from pvcomm.config.coverage import STRENGTHS

    router = APIRouter(prefix="/api/diagnostics/experiments", tags=["Oracle experiments"])

    def failure(exc: Exception) -> HTTPException:
        return HTTPException(
            status_code=404 if isinstance(exc, FileNotFoundError) else 409,
            detail="实验不存在" if isinstance(exc, FileNotFoundError) else "实验记录不可读取",
        )

    @router.get("/catalog", response_model=TemplateCatalog)
    def catalog() -> TemplateCatalog:
        return TemplateCatalog(
            strengths={k: tuple(v) for k, v in STRENGTHS.items()},
            submission_enabled=repository.root is not None,
            pair_submission_enabled=repository.root is not None and repository.matching is not None,
        )

    @router.get("", response_model=ExperimentList)
    def experiments(
        offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50)
    ) -> ExperimentList:
        try:
            identities = repository.ordered_identities()
            return ExperimentList(
                enabled=repository.root is not None,
                total=len(identities),
                experiments=[repository.view(i) for i in identities[offset : offset + limit]],
            )
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    @router.post("", response_model=ExperimentView, status_code=201)
    def submit(body: ExperimentRequest, request: Request) -> ExperimentView:
        if repository.root is None:
            raise HTTPException(status_code=503, detail="本地实验队列未启用")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="实验写入拒绝跨源请求")
        try:
            record = submit_request(repository.root, body, repository.matching)
            return repository.view(record["experiment_id"])
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail="实验模板或队列写入无效") from exc

    @router.get("/{identity}", response_model=ExperimentView)
    def experiment(identity: str) -> ExperimentView:
        try:
            return repository.view(identity)
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    @router.get("/{identity}/quality", response_model=ExperimentQuality)
    def quality(identity: str) -> ExperimentQuality:
        try:
            return repository.diagnostics(identity)
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    @router.get("/{identity}/runs/{run_key}/labels", response_model=LabelPage)
    def labels(
        identity: str,
        run_key: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=10, ge=1, le=30),
    ) -> LabelPage:
        try:
            return repository.labels(identity, run_key, offset, limit)
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    return router
