"""Versioned development power configuration, composed with accepted V0.1 lab."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import Field, model_validator

from pvcomm.config.lab import Positive, V01Config, V01Contract
from pvcomm.config.service import MAX_CONFIG_BYTES, UniqueKeyLoader, check_yaml_tree


class WeatherConfig(V01Contract):
    source_id: Literal["NASA_POWER_Hourly_huixian_20240601_30"]
    directory: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_start: datetime
    latitude: float = 35.4
    longitude: float = 113.8
    altitude_m: float = 330.63
    sampling: Literal["hourly_mean_zero_order_hold"] = "hourly_mean_zero_order_hold"

    @model_validator(mode="after")
    def check_location_time(self) -> Self:
        if self.source_start.tzinfo is None or self.source_start.utcoffset() != UTC.utcoffset(None):
            raise ValueError("source_start must explicitly use UTC")
        if (self.latitude, self.longitude, self.altitude_m) != (35.4, 113.8, 330.63):
            raise ValueError("source coordinates must match this versioned source")
        return self


class DeviceConfig(V01Contract):
    station_id: str
    rated_kw: Positive
    rated_kva: Positive
    dc_ac_ratio: Positive = 1.2
    tilt_degrees: Annotated[float, Field(ge=0, le=90)] = 30.0
    azimuth_degrees: Annotated[float, Field(ge=0, lt=360)] = 180.0
    gamma_pdc_per_c: Annotated[float, Field(ge=-0.01, lt=0)] = -0.004
    dc_loss_fraction: Annotated[float, Field(ge=0, lt=1)] = 0.14
    eta_inv_nom: Annotated[float, Field(gt=0, le=1)] = 0.96
    ramp_up_kw_s: Positive
    ramp_down_kw_s: Positive
    q_ramp_kvar_s: Positive
    q_target_kvar: float = 0.0
    initial_setpoint_kw: Annotated[float, Field(ge=0)]
    connected: bool = True
    enabled: bool = True
    faulted: bool = False

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.rated_kw > self.rated_kva or self.initial_setpoint_kw > self.rated_kw:
            raise ValueError("inverter rating or initial target is infeasible")
        if abs(self.q_target_kvar) > self.rated_kva:
            raise ValueError("Q target exceeds inverter rating")
        return self


class FeederConfig(V01Contract):
    parameter_basis: Literal["synthetic_engineering_assumption"]
    hv_kv: Positive = 20.0
    lv_kv: Positive = 0.4
    transformer_mva: Positive = 0.63
    transformer_vk_percent: Positive = 6.0
    transformer_vkr_percent: Positive = 1.2
    transformer_pfe_kw: Positive = 0.8
    transformer_i0_percent: Positive = 0.3
    line_length_km: Positive = 0.05
    line_r_ohm_km: Positive = 0.125
    line_x_ohm_km: Positive = 0.08
    line_max_i_ka: Positive = 0.6
    load_kw_per_bus: Positive = 50.0
    load_kvar_per_bus: Annotated[float, Field(ge=0)] = 10.0


class V02Config(V01Contract):
    schema_version: Literal["0.2"]
    purpose: Literal["power_protocol_development"]
    parameter_basis: Literal["simulation_assumption_not_field_calibrated"]
    protocol: V01Config
    weather: WeatherConfig
    devices: Annotated[tuple[DeviceConfig, ...], Field(min_length=3, max_length=3)]
    feeder: FeederConfig
    repeat_command_after_s: Annotated[int, Field(strict=True, ge=1)] | None = None
    electrical_step_s: Literal[1] = 1
    powerflow_step_s: Literal[5] = 5

    @model_validator(mode="after")
    def references(self) -> Self:
        lab = self.protocol
        if lab.topology != "isolated" or lab.interruption is not None:
            raise ValueError("V0.2 requires isolated normal three-site development runs")
        if tuple(d.station_id for d in self.devices) != tuple(s.station_id for s in lab.stations):
            raise ValueError("device IDs/order must match protocol stations")
        for device, station in zip(self.devices, lab.stations, strict=True):
            if (device.rated_kw, device.initial_setpoint_kw) != (
                station.capacity_kw,
                station.initial_setpoint_kw,
            ):
                raise ValueError("device and protocol capacity/initial target disagree")
            if station.available_kw != station.capacity_kw:
                raise ValueError(
                    "protocol available_kw is capacity only, not physical availability"
                )
        if (
            lab.command_after_s + 10 >= lab.release_after_s
            or lab.release_after_s + 10 >= lab.duration_s
        ):
            raise ValueError("allow independent execution/feedback windows")
        if self.repeat_command_after_s is not None and not (
            lab.command_after_s + 10 < self.repeat_command_after_s < lab.release_after_s - 10
        ):
            raise ValueError("repeat command needs disjoint execution observation windows")
        return self


def load_power_config(path: Path) -> V02Config:
    data = path.read_bytes()
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("config exceeds maximum size")
    raw = yaml.load(data.decode("utf-8"), Loader=UniqueKeyLoader)
    check_yaml_tree(raw)
    return V02Config.model_validate(raw)
