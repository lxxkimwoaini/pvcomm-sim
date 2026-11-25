"""Local configuration and read-only M2 observation service."""

import json
import os
import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, validate_yaml
from pvcomm.logging import create_logger
from pvcomm.web.runs import RunList, RunRepository, RunSnapshot
from pvcomm.web.schemas import Health, ValidationRequest, ValidationResult


def create_app(run_root: Path | None = None) -> FastAPI:
    app = FastAPI(title="PVComm foundation API", version=__version__)
    repository = RunRepository(run_root or Path(os.environ.get("PVCOMM_RUN_ROOT", "outputs")))
    logger = create_logger("pvcomm.web.runs", sys.stderr)

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

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health()

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
