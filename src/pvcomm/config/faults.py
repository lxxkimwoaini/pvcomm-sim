"""Oracle-only single-fault composition; old normal scene contracts stay intact."""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import Field, field_validator, model_validator

from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.scenes import ScenarioConfig, instantiate_scenario
from pvcomm.config.service import MAX_CONFIG_BYTES, UniqueKeyLoader, check_yaml_tree


class StationScope(V01Contract):
    kind: Literal["station"]
    station_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=120)]


class RegionScope(V01Contract):
    kind: Literal["region"]
    region_id: Identifier


class GlobalScope(V01Contract):
    kind: Literal["global"]


FaultScope = Annotated[StationScope | RegionScope | GlobalScope, Field(discriminator="kind")]


class LinkInterruption(V01Contract):
    event_id: Identifier
    fault_type: Literal["A01"]
    start_s: Annotated[int, Field(strict=True, ge=1)]
    duration_s: Literal[5, 20, 60]
    scope: FaultScope

    @field_validator("duration_s", mode="before")
    @classmethod
    def strict_candidate(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("A01 duration must be an integer candidate")
        return value


class LinkDelay(V01Contract):
    event_id: Identifier
    fault_type: Literal["A02"]
    start_s: Annotated[int, Field(strict=True, ge=1)]
    duration_s: Annotated[int, Field(strict=True, ge=10)]
    additional_delay_ms: Literal[200, 1000, 3000]
    direction: Literal["master_to_rtu", "rtu_to_master"]
    scope: FaultScope

    @field_validator("additional_delay_ms", mode="before")
    @classmethod
    def strict_candidate(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("A02 delay must be an integer candidate in milliseconds")
        return value


class FaultScenarioConfig(V01Contract):
    schema_version: Literal["0.4-fault.1", "0.4-fault.2"]
    purpose: Literal["single_fault_development"]
    background: ScenarioConfig
    fault: Annotated[LinkInterruption | LinkDelay, Field(discriminator="fault_type")]

    def target_stations(self) -> tuple[str, ...]:
        stations = self.background.simulation.protocol.stations
        scope = self.fault.scope
        if isinstance(scope, StationScope):
            return tuple(s.station_id for s in stations if s.station_id in scope.station_ids)
        if isinstance(scope, RegionScope):
            return tuple(s.station_id for s in stations if s.region_id == scope.region_id)
        return tuple(s.station_id for s in stations)

    @model_validator(mode="after")
    def references_and_recovery(self) -> Self:
        protocol = self.background.simulation.protocol
        if protocol.interruption is not None:
            raise ValueError("single fault cannot contain a second maintenance/interruption event")
        if isinstance(self.fault, LinkDelay):
            if self.schema_version != "0.4-fault.2":
                raise ValueError("A02 requires fault contract 0.4-fault.2")
            simulation = self.background.simulation
            links = (
                [s.access_link for s in protocol.stations if s.station_id in self.target_stations()]
                if isinstance(self.fault.scope, StationScope)
                else [
                    r.uplink
                    for r in simulation.regions
                    if self.fault.scope.kind == "global"
                    or r.region_id == self.fault.scope.region_id
                ]
            )
            if not links or any(link is None for link in links):
                raise ValueError("A02 requires explicit NetEm baseline on every selected link")
        scope = self.fault.scope
        if isinstance(scope, StationScope) and (
            len(set(scope.station_ids)) != len(scope.station_ids)
            or set(scope.station_ids) != set(self.target_stations())
        ):
            raise ValueError("unknown or duplicate fault station")
        if not self.target_stations():
            raise ValueError("fault scope has no configured stations")
        if (
            self.fault.start_s < protocol.warmup_s
            or self.fault.start_s + self.fault.duration_s + 20 >= protocol.duration_s
        ):
            raise ValueError("fault requires warmup and a complete recovery observation window")
        return self


def load_fault_scenario(path: Path) -> FaultScenarioConfig:
    content = path.read_bytes()
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("fault scenario exceeds maximum size")
    try:
        raw = yaml.load(content.decode("utf-8"), Loader=UniqueKeyLoader)
        check_yaml_tree(raw)
        return FaultScenarioConfig.model_validate(raw)
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid fault scenario {path}: {exc}") from exc


def instantiate_fault(template: FaultScenarioConfig, run_id: str) -> FaultScenarioConfig:
    payload = template.model_dump(mode="json")
    payload["background"] = instantiate_scenario(template.background, run_id).model_dump(
        mode="json"
    )
    return FaultScenarioConfig.model_validate(payload)
