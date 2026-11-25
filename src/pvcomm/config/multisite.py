"""V0.3 development contracts; accepted 0.1/0.2 documents remain unchanged."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pvcomm.config.lab import Identifier, Positive, V01Config, V01Contract, V01StationConfig
from pvcomm.config.power import DeviceConfig, FeederConfig, WeatherConfig


class SiteConfig(V01StationConfig):
    x_m: float
    y_m: float
    feeder_id: Identifier
    address: str = Field(pattern=r"^10\.204\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})$")
    point_table: Literal["pvcomm-v02"] = "pvcomm-v02"
    telemetry_phase_s: Annotated[float, Field(ge=0)] = 0.0
    deadband_fraction: Annotated[float, Field(ge=0.005, le=0.02)] = 0.01

    @model_validator(mode="after")
    def phase_valid(self) -> Self:
        if self.telemetry_phase_s >= self.telemetry_period_s:
            raise ValueError("telemetry phase must precede its period")
        return self


class MultisiteProtocol(V01Config):
    stations: Annotated[tuple[SiteConfig, ...], Field(min_length=1, max_length=120)]


class RegionConfig(V01Contract):
    region_id: Identifier


class FeederGroup(V01Contract):
    feeder_id: Identifier
    parameters: FeederConfig


class SpatialCloud(V01Contract):
    direction_degrees: Annotated[float, Field(ge=0, lt=360)]
    crosswind_width_m: Positive


class DispatchTask(V01Contract):
    dispatch_id: Identifier
    at_s: Annotated[int, Field(strict=True, ge=1)]
    region_ids: tuple[Identifier, ...]
    total_kw: Annotated[float, Field(ge=0)]
    allocation: Literal["capacity", "observed_available"]
    batch_size: Annotated[int, Field(strict=True, ge=1, le=30)] = 5
    batch_interval_s: Annotated[float, Field(ge=0)] = 1.0


class V03Config(V01Contract):
    schema_version: Literal["0.3"]
    purpose: Literal["multisite_power_protocol_development"]
    parameter_basis: Literal["simulation_assumption_not_field_calibrated"]
    protocol: MultisiteProtocol
    weather: WeatherConfig
    weather_by_station: dict[str, WeatherConfig] = Field(default_factory=dict)
    devices: Annotated[tuple[DeviceConfig, ...], Field(min_length=1, max_length=120)]
    regions: Annotated[tuple[RegionConfig, ...], Field(min_length=1, max_length=12)]
    feeders: Annotated[tuple[FeederGroup, ...], Field(min_length=1, max_length=12)]
    spatial_cloud: SpatialCloud | None = None
    dispatch_tasks: tuple[DispatchTask, ...] = ()
    repeat_command_after_s: Annotated[int, Field(strict=True, ge=1)] | None = None
    electrical_step_s: Literal[1] = 1
    powerflow_step_s: Literal[5] = 5

    @model_validator(mode="after")
    def references(self) -> Self:
        import ipaddress

        lab = self.protocol
        if not set(self.weather_by_station).issubset({s.station_id for s in lab.stations}):
            raise ValueError("weather override references unknown station")
        if lab.topology != "isolated":
            raise ValueError("V0.3 requires isolated topology")
        if tuple(d.station_id for d in self.devices) != tuple(s.station_id for s in lab.stations):
            raise ValueError("device IDs/order must match stations")
        regions = {r.region_id for r in self.regions}
        feeders = {f.feeder_id for f in self.feeders}
        if len(regions) != len(self.regions) or len(feeders) != len(self.feeders):
            raise ValueError("duplicate region/feeder ID")
        addresses = set()
        for device, site in zip(self.devices, lab.stations, strict=True):
            if site.region_id not in regions or site.feeder_id not in feeders:
                raise ValueError("unknown region/feeder reference")
            region_index = next(
                i for i, r in enumerate(self.regions, 1) if r.region_id == site.region_id
            )
            address = ipaddress.IPv4Address(site.address)
            if (
                address not in ipaddress.IPv4Network(f"10.204.{region_index}.0/24")
                or int(address) % 256 < 2
                or int(address) % 256 > 253
            ):
                raise ValueError("site address must be a usable member of its region subnet")
            if site.address in addresses:
                raise ValueError("duplicate site address")
            addresses.add(site.address)
            if (device.rated_kw, device.initial_setpoint_kw) != (
                site.capacity_kw,
                site.initial_setpoint_kw,
            ) or site.available_kw != site.capacity_kw:
                raise ValueError("protocol capacity/initial target must match device")
        if {s.region_id for s in lab.stations} != regions or {
            s.feeder_id for s in lab.stations
        } != feeders:
            raise ValueError("empty region/feeder")
        if (
            lab.command_after_s + 10 >= lab.release_after_s
            or lab.release_after_s + 10 >= lab.duration_s
        ):
            raise ValueError("allow disjoint execution windows")
        if (
            self.repeat_command_after_s is not None
            and not lab.command_after_s + 10
            < self.repeat_command_after_s
            < lab.release_after_s - 10
        ):
            raise ValueError("repeat command needs disjoint execution windows")
        if self.spatial_cloud is not None and self.weather.cloud_shadow is None:
            raise ValueError("spatial cloud requires an explicit modeled optical layer")
        if len({t.dispatch_id for t in self.dispatch_tasks}) != len(self.dispatch_tasks):
            raise ValueError("dispatch IDs must be unique")
        previous_end = 0.0
        for task in self.dispatch_tasks:
            if (
                not task.region_ids
                or len(set(task.region_ids)) != len(task.region_ids)
                or not set(task.region_ids).issubset(regions)
            ):
                raise ValueError("invalid dispatch regions")
            members = [s for s in lab.stations if s.region_id in task.region_ids]
            end = (
                task.at_s + (len(members) - 1) // task.batch_size * (3 + task.batch_interval_s) + 60
            )
            if task.at_s < previous_end or end >= lab.duration_s:
                raise ValueError("dispatch execution windows overlap or exceed run")
            previous_end = end
        return self
