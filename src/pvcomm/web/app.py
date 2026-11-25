"""Local configuration service, without experiment resources or run access."""

import json

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, validate_yaml
from pvcomm.web.schemas import Health, ValidationRequest, ValidationResult


def create_app() -> FastAPI:
    app = FastAPI(title="PVComm foundation API", version=__version__)

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
