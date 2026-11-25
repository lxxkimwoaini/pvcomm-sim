"""Service capability contract; CLI owns simulation execution."""

from typing import Literal

from pydantic import Field

from pvcomm.config.models import Contract


class Health(Contract):
    status: Literal["ok"] = "ok"
    stage: Literal["V0.3-development"] = "V0.3-development"
    simulation_available: Literal[True] = True
    web_run_submission_available: Literal[False] = False


class ValidationRequest(Contract):
    yaml_text: str = Field(max_length=131072)


class ValidationResult(Contract):
    valid: Literal[True] = True
    schema_version: str
    config_sha256: str
    station_count: int
