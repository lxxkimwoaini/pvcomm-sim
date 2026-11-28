"""Local configuration and read-only M2 observation service."""

import json
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, validate_yaml
from pvcomm.config.batch import MatchingInputs
from pvcomm.logging import create_logger
from pvcomm.web.experiments import ExperimentRepository, experiment_router
from pvcomm.web.pair_replay import PairReplayRepository, pair_replay_router
from pvcomm.web.runs import EventsPage, ResourceView, RunList, RunRepository, RunSnapshot
from pvcomm.web.schemas import Health, ValidationRequest, ValidationResult


def create_app(run_root: Path | None = None, experiment_root: Path | None = None) -> FastAPI:
    app = FastAPI(title="PVComm foundation API", version=__version__)
    repository = RunRepository(run_root or Path(os.environ.get("PVCOMM_RUN_ROOT", "outputs")))
    logger = create_logger("pvcomm.web.runs", sys.stderr)
    configured_experiments = os.environ.get("PVCOMM_EXPERIMENT_ROOT")
    policy = os.environ.get("PVCOMM_SIMILARITY_POLICY")
    calibration = os.environ.get("PVCOMM_SIMILARITY_CALIBRATION")
    if bool(policy) != bool(calibration):
        raise ValueError("pair management requires both frozen policy and calibration")
    matching = (
        MatchingInputs.model_validate(
            dict(
                policy=json.loads(Path(policy).read_bytes()),
                calibration=json.loads(Path(calibration).read_bytes()),
            )
        )
        if policy and calibration
        else None
    )
    experiments = ExperimentRepository(
        experiment_root or (Path(configured_experiments) if configured_experiments else None),
        matching,
    )
    app.include_router(experiment_router(experiments))
    archive = os.environ.get("PVCOMM_PAIR_REPLAY_ROOT")
    app.include_router(
        pair_replay_router(
            PairReplayRepository(experiments.root, Path(archive) if archive else None)
        )
    )

    @app.middleware("http")
    async def request_timing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.3f}"
        return response

    @app.get("/api/runs", response_model=RunList)
    def runs() -> RunList:
        try:
            return repository.list_runs()
        except (OSError, ValueError) as exc:
            logger.exception("run listing failed", extra={"event": "run_list_failed"})
            raise HTTPException(status_code=409, detail="运行清单暂不可读取") from exc

    @app.get("/api/runs/{run_key}/snapshot", response_model=RunSnapshot)
    def snapshot(
        run_key: str, at_ns: int | None = Query(default=None, ge=0), mode: Literal["M2"] = "M2"
    ) -> RunSnapshot:
        try:
            return repository.snapshot(run_key, at_ns)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="运行不存在") from exc
        except (OSError, ValueError) as exc:
            logger.exception("observation read failed", extra={"event": "run_snapshot_failed"})
            raise HTTPException(
                status_code=409, detail="运行证据暂不可读取，请检查服务端日志"
            ) from exc

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> Response:
        # Preserve the OpenAPI error shape without echoing user data. ASCII escaping
        # also handles unpaired surrogates that the default JSON response cannot encode.
        details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return Response(
            content=json.dumps({"detail": details}),
            status_code=422,
            media_type="application/json",
        )

    @app.get("/api/runs/{run_key}/events", response_model=EventsPage)
    def events(
        run_key: str,
        at_ns: int = Query(ge=0),
        before_ns: int | None = Query(default=None, ge=0),
        before_id: str | None = Query(default=None, min_length=1, max_length=256),
        limit: int = Query(default=100, ge=1, le=100),
        mode: Literal["M2"] = "M2",
    ) -> EventsPage:
        if (before_ns is None) != (before_id is None):
            raise HTTPException(status_code=422, detail="分页游标必须同时包含时间和事件ID")
        try:
            return repository.event_page(
                run_key,
                at_ns,
                (before_ns, before_id) if before_ns is not None and before_id is not None else None,
                limit,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="运行不存在") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="观测证据不可读取") from exc

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(web_run_submission_available=experiments.root is not None)

    @app.get("/api/diagnostics/runs/{run_key}/resources", response_model=ResourceView)
    def resources(run_key: str) -> ResourceView:
        try:
            return repository.resources(run_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="运行不存在") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="资源诊断不可读取") from exc

    @app.post("/api/scenarios/validate", response_model=ValidationResult)
    def validate(request: ValidationRequest) -> ValidationResult:
        try:
            config = validate_yaml(request.yaml_text)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ValidationResult(
            schema_version=config.schema_version,
            config_sha256=config_sha256(config),
            station_count=len(config.stations),
        )

    return app
