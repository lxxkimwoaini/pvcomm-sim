"""Explicit Oracle management projection, separate from the M2 run repository."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from pvcomm.config.batch import MatchingInputs
from pvcomm.config.models import Contract
from pvcomm.config.normal_scenes import SCENE_CODES
from pvcomm.config.workbench import (
    DEVELOPMENT_SITE_COUNTS,
    FAULT_CODES,
    PAIR_CODES,
    ExperimentRequest,
)
from pvcomm.domain.labels import EvidenceRef
from pvcomm.pipeline.labels import file_sha256
from pvcomm.scenario.requests import SCOPE, read_json, safe_file, submit_request
from pvcomm.web.diagnostic_index import DiagnosticIndexes, precise


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
    site_counts: tuple[int, ...] = DEVELOPMENT_SITE_COUNTS
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
    evidence_links: list[dict[str, Any]] = []


class EvidenceView(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    reference: dict[str, Any]
    source_hash_verified: bool
    record: dict[str, Any] | None
    capture: dict[str, Any] | None = None


class ExportInventory(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    run_id: str
    code_version: str | None
    files: list[dict[str, Any]]
    offset: int
    total: int
    original_evidence_retained: Literal[True] = True


def label_references(label: dict[str, Any]) -> dict[str, dict[str, Any]]:
    refs = label.get("evidence", []) + [
        ref for finding in label.get("findings", []) for ref in finding.get("evidence", [])
    ]
    return {
        hashlib.sha256(json.dumps(ref, sort_keys=True).encode()).hexdigest()[:24]: ref
        for ref in refs
    }


def opaque_key(path: Path, root: Path) -> str:
    return hashlib.sha256(path.relative_to(root).as_posix().encode()).hexdigest()[:24]


class ExperimentRepository:
    def __init__(self, root: Path | None, matching: MatchingInputs | None = None) -> None:
        self.root = root.resolve() if root else None
        # Deep frozen evidence paths exceed legacy Win32 MAX_PATH, especially
        # through the WSL UNC share. Use the explicit filesystem namespace.
        if os.name == "nt" and self.root is not None:
            resolved = str(self.root)
            if not resolved.startswith("\\\\?\\"):
                self.root = Path(
                    "\\\\?\\UNC\\" + resolved[2:]
                    if resolved.startswith("\\\\")
                    else "\\\\?\\" + resolved
                )
        self.matching = matching
        self.indexes = DiagnosticIndexes()

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
            status="deferred_scope"
            if worker.get("status") == "deferred_scope"
            else queue["status"]
            if queue
            else worker.get("status", "queued"),
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
        directory = self.run_directory(identity, run_key)
        assert self.root is not None
        manifest = read_json(self.root, directory / "manifest.json")
        path = safe_file(self.root, directory / "labels/v04-label-v3/event_labels.jsonl")
        with self.indexes.lock:
            index = self.indexes.get(path, manifest["run_id"])
            labels = index.page(offset, limit)
            links = [
                dict(label_ordinal=offset + i, evidence_key=key, reference=ref)
                for i, label in enumerate(labels)
                for key, ref in label_references(label).items()
            ]
            return LabelPage(
                labels=precise(labels), total=index.total, evidence_links=precise(links)
            )

    def run_directory(self, identity: str, run_key: str) -> Path:
        runs = self.run_directories(identity)
        if run_key not in runs:
            raise FileNotFoundError("unknown run")
        return runs[run_key]

    def evidence(self, identity: str, run_key: str, ordinal: int, key: str) -> EvidenceView:
        directory = self.run_directory(identity, run_key)
        assert self.root is not None
        manifest = read_json(self.root, directory / "manifest.json")
        with self.indexes.lock:
            labels = self.indexes.get(
                safe_file(self.root, directory / "labels/v04-label-v3/event_labels.jsonl"),
                manifest["run_id"],
            ).page(ordinal, 1)
            refs = label_references(labels[0]) if labels else {}
            if key not in refs:
                raise FileNotFoundError("unknown evidence reference")
            ref = EvidenceRef.model_validate(refs[key])
            path = safe_file(directory, directory / ref.path)
            record = None
            if ref.record_id is not None:
                if path.suffix != ".jsonl":
                    raise ValueError("record references require JSONL")
                index = self.indexes.get(path, manifest["run_id"])
                if index.sha256 != ref.sha256:
                    raise ValueError("evidence checksum changed")
                record = index.record(ref.record_id)
            else:
                if file_sha256(path) != ref.sha256:
                    raise ValueError("evidence checksum changed")
                if path.suffix == ".json":
                    record = read_json(directory, path)
            capture = None
            if record and record.get("capture_id") == "primary":
                captures = [c for c in manifest["captures"] if c["path"] == "raw/pcap/primary.pcap"]
                if len(captures) != 1:
                    raise ValueError("ambiguous primary capture")
                frames = record["source_frame_refs"]
                if not frames or any(type(frame) is not int or frame <= 0 for frame in frames):
                    raise ValueError("invalid capture frame references")
                capture = dict(
                    **captures[0],
                    file_key=opaque_key(directory / captures[0]["path"], directory),
                    frame_numbers=frames,
                    wireshark_filter="frame.number in {" + " ".join(map(str, frames)) + "}",
                )
            return EvidenceView(
                reference=precise(ref.model_dump()),
                source_hash_verified=True,
                record=precise(record),
                capture=capture,
            )

    def export_files(self, identity: str, run_key: str) -> tuple[Path, dict[str, Path]]:
        directory = self.run_directory(identity, run_key)
        assert self.root is not None
        if read_json(self.root, directory / "manifest.json")["status"] not in (
            "completed",
            "failed",
            "cancelled",
        ):
            raise ValueError("export requires a finalized run")
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("export refuses symbolic links")
            if path.is_file() and not any(
                p.startswith(".") for p in path.relative_to(directory).parts
            ):
                files[opaque_key(path, directory)] = safe_file(directory, path)
        return directory, files

    def inventory(
        self, identity: str, run_key: str, offset: int = 0, limit: int = 50
    ) -> ExportInventory:
        directory, files = self.export_files(identity, run_key)
        manifest = read_json(directory, directory / "manifest.json")
        return ExportInventory(
            run_id=manifest["run_id"],
            code_version=manifest.get("code_version"),
            offset=offset,
            total=len(files),
            files=[
                dict(
                    file_key=key,
                    path=path.relative_to(directory).as_posix(),
                    size_bytes=path.stat().st_size,
                    sha256=file_sha256(path),
                )
                for key, path in list(files.items())[offset : offset + limit]
            ],
        )


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

    @router.get(
        "/{identity}/runs/{run_key}/labels/{ordinal}/evidence/{key}", response_model=EvidenceView
    )
    def evidence(identity: str, run_key: str, ordinal: int, key: str) -> EvidenceView:
        if ordinal < 0:
            raise HTTPException(status_code=422, detail="标签序号无效")
        try:
            return repository.evidence(identity, run_key, ordinal, key)
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    @router.get("/{identity}/runs/{run_key}/export", response_model=ExportInventory)
    def inventory(
        identity: str,
        run_key: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> ExportInventory:
        try:
            return repository.inventory(identity, run_key, offset, limit)
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    @router.get("/{identity}/runs/{run_key}/export/{file_key}")
    def download(identity: str, run_key: str, file_key: str) -> FileResponse:
        try:
            directory, files = repository.export_files(identity, run_key)
            if file_key not in files:
                raise FileNotFoundError("unknown export file")
            path = files[file_key]
            digest = file_sha256(path)
            manifest = read_json(directory, directory / "manifest.json")
            for capture in manifest.get("captures", []):
                if (
                    path.relative_to(directory).as_posix() == capture["path"]
                    and digest != capture["sha256"]
                ):
                    raise ValueError("capture checksum changed")
            return FileResponse(
                path,
                filename=path.name,
                media_type="application/octet-stream",
                headers={"X-Content-SHA256": digest},
            )
        except (OSError, ValueError, KeyError) as exc:
            raise failure(exc) from exc

    return router
