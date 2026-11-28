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


class TerminalFault(V01Contract):
    event_id: Identifier
    fault_type: Literal["A04", "A05", "A06", "A08"]
    start_s: Annotated[int, Field(strict=True, ge=1)]
    duration_s: Annotated[int, Field(strict=True, ge=1)]
    scope: FaultScope
    missing_cycles: Literal[2, 5, 12] | None = None
    response_delay_s: Literal[2, 5, 15] | None = None
    feedback_duration_s: Literal[10, 30, 60] | None = None
    recovery_cycles: Literal[1, 3, 5] | None = None

    @field_validator(
        "missing_cycles",
        "response_delay_s",
        "feedback_duration_s",
        "recovery_cycles",
        mode="before",
    )
    @classmethod
    def strict_strength(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("terminal fault strength must be an integer")
        return value

    @model_validator(mode="after")
    def strength(self) -> Self:
        names = {
            "A04": "missing_cycles",
            "A05": "response_delay_s",
            "A06": "feedback_duration_s",
            "A08": "recovery_cycles",
        }
        if [name for name in names.values() if getattr(self, name) is not None] != [
            names[self.fault_type]
        ]:
            raise ValueError("exactly the selected terminal mechanism strength is required")
        return self


class FaultScenarioConfig(V01Contract):
    schema_version: Literal["0.4-fault.1", "0.4-fault.2", "0.4-fault.3"]
    purpose: Literal["single_fault_development"]
    background: ScenarioConfig
    fault: Annotated[
        LinkInterruption | LinkDelay | TerminalFault, Field(discriminator="fault_type")
    ]

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
        if self.background.expanded_maintenance() and self.fault.fault_type != "A08":
            raise ValueError("single fault cannot contain a second maintenance/interruption event")
        if isinstance(self.fault, TerminalFault):
            fault = self.fault
            if self.schema_version != "0.4-fault.3":
                raise ValueError("terminal mechanisms require fault contract 0.4-fault.3")
            if fault.fault_type == "A04":
                expected = max(
                    s.telemetry_period_s
                    for s in protocol.stations
                    if s.station_id in self.target_stations()
                ) * int(fault.missing_cycles or 0)
                if fault.duration_s != expected:
                    raise ValueError(
                        "A04 duration must equal selected maximum report period times cycles"
                    )
            if fault.fault_type == "A06" and fault.duration_s != fault.feedback_duration_s:
                raise ValueError("A06 hold duration must match its strength")
            if fault.fault_type == "A05":
                self.check_delayed_confirmation_support()
            if fault.fault_type == "A08":
                maintenance = self.background.expanded_maintenance()
                targets = set(self.target_stations())
                if (
                    not targets
                    or not targets.issubset({m.station_id for m in maintenance if m.planned})
                    or any(
                        fault.start_s != m.start_s + m.duration_s
                        for m in maintenance
                        if m.station_id in targets
                    )
                    or fault.duration_s != 15 * int(fault.recovery_cycles or 0)
                ):
                    raise ValueError(
                        "A08 requires matching planned recovery for every target and 15s cycles"
                    )
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

    def check_delayed_confirmation_support(self) -> None:
        """c104 2.2.1 deferred ACT_CON has no public negative-confirmation setter."""
        from pvcomm.config.scenes import AllocationTask, SetpointTask

        fault = self.fault
        if not isinstance(fault, TerminalFault) or fault.fault_type != "A05":
            return
        for task in self.background.expanded_schedule():
            if not isinstance(task, (SetpointTask, AllocationTask)):
                continue
            span = (
                (len(task.station_ids) - 1) // task.batch_size * (3 + task.batch_interval_s)
                if isinstance(task, AllocationTask)
                else 0
            )
            if task.at_s + span < fault.start_s or task.at_s >= fault.start_s + fault.duration_s:
                continue
            left, right = (
                task.at_s + int(fault.response_delay_s or 0),
                task.at_s + span + int(fault.response_delay_s or 0),
            )
            for station in set(task.station_ids) & set(self.target_stations()):
                # Test every state boundary in the possible deferred execution interval.
                operations = sorted(
                    (
                        op
                        for op in self.background.simulation.device_operations
                        if op.station_id == station
                    ),
                    key=lambda op: op.at_s,
                )
                device = next(
                    d for d in self.background.simulation.devices if d.station_id == station
                )
                state: dict[str, object] = {
                    "enabled": device.enabled,
                    "connected": device.connected,
                    "faulted": device.faulted,
                }
                for op in operations:
                    if op.at_s > right:
                        break
                    if op.at_s > left and (
                        not state["enabled"] or not state["connected"] or state["faulted"]
                    ):
                        raise ValueError(
                            "A05 delayed negative confirmation is unsupported "
                            "by c104 2.2.1 public API"
                        )
                    for name in state:
                        value = getattr(op, name, None)
                        if value is not None:
                            state[name] = value
                    if op.at_s >= left and (
                        not state["enabled"] or not state["connected"] or state["faulted"]
                    ):
                        raise ValueError(
                            "A05 delayed negative confirmation is unsupported "
                            "by c104 2.2.1 public API"
                        )
                if not state["enabled"] or not state["connected"] or state["faulted"]:
                    raise ValueError(
                        "A05 delayed negative confirmation is unsupported by c104 2.2.1 public API"
                    )


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
