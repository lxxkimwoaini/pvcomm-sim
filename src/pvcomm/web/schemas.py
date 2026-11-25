"""V0.0 validation API; no runtime or truth query contracts yet."""

from typing import Literal

from pydantic import Field

from pvcomm.config.models import Contract


class Health(Contract):
    status: Literal["ok"] = "ok"
    stage: Literal["V0.0"] = "V0.0"
    simulation_available: Literal[False] = False


class ValidationRequest(Contract):
    yaml_text: str = Field(max_length=131072)


class ValidationResult(Contract):
    valid: Literal[True] = True
    schema_version: str
    config_sha256: str
    station_count: int
