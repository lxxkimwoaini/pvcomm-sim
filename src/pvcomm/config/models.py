"""Version 0.0 foundation validation; not the full runtime scenario contract."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ClockConfig(Contract):
    mode: Literal["realtime"] = "realtime"
    speed: Annotated[float, Field(ge=1.0, le=1.0, strict=True)] = 1.0
    duration_s: Annotated[int, Field(strict=True, gt=0)]
    warmup_s: Annotated[int, Field(strict=True, ge=0)]
    electrical_step_s: Literal[1] = 1
    powerflow_step_s: Literal[5] = 5

    @field_validator("speed", "electrical_step_s", "powerflow_step_s", mode="before")
    @classmethod
    def reject_coerced_numbers(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("clock numeric fields require numbers, not booleans or strings")
        return value

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if self.warmup_s >= self.duration_s:
            raise ValueError("warmup_s must be less than duration_s")
        return self


class DeviceProfile(Contract):
    profile_id: Identifier
    telemetry_period_s: Literal[2, 5, 10]
    ack_deadline_s: Positive

    @field_validator("telemetry_period_s", mode="before")
    @classmethod
    def require_integer_period(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("telemetry_period_s requires an integer")
        return value


class Station(Contract):
    station_id: Identifier
    region_id: Identifier
    feeder_id: Identifier
    device_profile: Identifier
    common_address: Annotated[int, Field(strict=True, ge=1, le=65534)]
    capacity_kw: Positive
    rated_kva: Positive

    @model_validator(mode="after")
    def check_capacity(self) -> Self:
        if self.rated_kva < self.capacity_kw:
            raise ValueError("rated_kva must cover rated active capacity_kw")
        return self


class FoundationConfig(Contract):
    schema_version: Literal["0.0"]
    purpose: Literal["foundation_validation"]
    parameter_basis: Literal["simulation_assumption"]
    run_id: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    clock: ClockConfig
    regions: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    feeders: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    device_profiles: Annotated[tuple[DeviceProfile, ...], Field(min_length=1)]
    stations: Annotated[tuple[Station, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        groups = {
            "region": self.regions,
            "feeder": self.feeders,
            "profile": tuple(p.profile_id for p in self.device_profiles),
            "station": tuple(s.station_id for s in self.stations),
            "common_address": tuple(s.common_address for s in self.stations),
        }
        for name, values in groups.items():
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {name}")
        for station in self.stations:
            if station.region_id not in self.regions:
                raise ValueError(f"unknown region: {station.region_id}")
            if station.feeder_id not in self.feeders:
                raise ValueError(f"unknown feeder: {station.feeder_id}")
            if station.device_profile not in groups["profile"]:
                raise ValueError(f"unknown device profile: {station.device_profile}")
        return self
