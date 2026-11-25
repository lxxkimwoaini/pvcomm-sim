"""Strict V0.1 local IEC 104 lab configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pvcomm.config.service import MAX_CONFIG_BYTES, UniqueKeyLoader, check_yaml_tree

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]


class V01Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class V01StationConfig(V01Contract):
    station_id: Identifier
    region_id: Identifier
    common_address: Annotated[int, Field(strict=True, ge=1, le=65534)]
    port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    capacity_kw: Positive
    available_kw: Positive
    initial_setpoint_kw: Positive
    ramp_kw_per_s: Positive
    reactive_kvar: Annotated[float, Field(strict=True)] = 0.0
    voltage_kv: Positive = 0.4
    telemetry_period_s: Annotated[int, Field(strict=True, ge=2, le=10)]

    @field_validator("telemetry_period_s")
    @classmethod
    def check_period(cls, value: int) -> int:
        if value not in (2, 5, 10):
            raise ValueError("telemetry_period_s must be 2, 5 or 10")
        return value

    @model_validator(mode="after")
    def check_power_bounds(self) -> Self:
        if self.available_kw > self.capacity_kw:
            raise ValueError("available_kw must not exceed capacity_kw")
        if self.initial_setpoint_kw > self.available_kw:
            raise ValueError("initial_setpoint_kw must not exceed available_kw")
        return self


class V01Config(V01Contract):
    schema_version: Literal["0.1"]
    purpose: Literal["iec104_lab"]
    parameter_basis: Literal["simulation_assumption"]
    run_id: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    host: Literal["127.0.0.1"] = "127.0.0.1"
    capture_interface: Literal["lo"] = "lo"
    capture_filter: str = "tcp"
    duration_s: Annotated[int, Field(strict=True, ge=5, le=86_400)]
    warmup_s: Annotated[int, Field(strict=True, ge=1)]
    command_after_s: Annotated[int, Field(strict=True, ge=1)]
    release_after_s: Annotated[int, Field(strict=True, ge=2)]
    command_kw: Positive
    stations: Annotated[tuple[V01StationConfig, ...], Field(min_length=3, max_length=3)]

    @field_validator("capture_filter")
    @classmethod
    def reject_shell_syntax(cls, value: str) -> str:
        if any(token in value for token in (";", "&&", "|", ">", "<", "`", "$")):
            raise ValueError("capture_filter contains shell syntax")
        return value

    @model_validator(mode="after")
    def check_timeline(self) -> Self:
        if self.warmup_s >= self.command_after_s:
            raise ValueError("warmup_s must be less than command_after_s")
        if self.command_after_s >= self.release_after_s:
            raise ValueError("command_after_s must be less than release_after_s")
        if self.release_after_s >= self.duration_s:
            raise ValueError("release_after_s must be less than duration_s")
        if self.command_kw <= 0:
            raise ValueError("command_kw must be positive")
        if len({station.port for station in self.stations}) != len(self.stations):
            raise ValueError("station ports must be unique")
        if len({station.common_address for station in self.stations}) != len(self.stations):
            raise ValueError("station common addresses must be unique")
        if len({station.station_id for station in self.stations}) != len(self.stations):
            raise ValueError("station IDs must be unique")
        for station in self.stations:
            if self.command_kw > station.available_kw:
                raise ValueError(f"command_kw exceeds available_kw for {station.station_id}")
        return self


def load_v01_config(path: Path) -> V01Config:
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            raise ValueError("config exceeds maximum size")
        raw = yaml.load(content.decode("utf-8"), Loader=UniqueKeyLoader)
        check_yaml_tree(raw)
        return V01Config.model_validate(raw)
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid V0.1 config {path}: {exc}") from exc


def v01_config_sha256(config: V01Config) -> str:
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
