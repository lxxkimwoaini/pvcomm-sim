"""Explicit V0.4 business schedule, composed with the frozen V0.3 contract."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.maintenance import MaintenanceWindow, StationMaintenance, legacy_maintenance
from pvcomm.config.multisite import V03Config
from pvcomm.config.service import MAX_CONFIG_BYTES, UniqueKeyLoader, check_yaml_tree


class ScheduledTask(V01Contract):
    schedule_id: Identifier
    at_s: Annotated[int, Field(strict=True, ge=1)]
    jitter_s: Annotated[int, Field(strict=True, ge=0, le=300)] = 0
    station_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=120)]


class SetpointTask(ScheduledTask):
    kind: Literal["setpoint"]
    targets_kw: dict[Identifier, Annotated[float, Field(ge=0)]]


class AllocationTask(ScheduledTask):
    kind: Literal["dispatch"]
    total_kw: Annotated[float, Field(ge=0)]
    allocation: Literal["capacity", "observed_available"]
    batch_size: Annotated[int, Field(strict=True, ge=1, le=30)] = 5
    batch_interval_s: Annotated[float, Field(ge=0)] = 1.0


class InterrogationTask(ScheduledTask):
    kind: Literal["interrogation"]


BusinessTask = Annotated[
    SetpointTask | AllocationTask | InterrogationTask, Field(discriminator="kind")
]


def canonical_hash(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


class ScenarioConfig(V01Contract):
    schema_version: Literal["0.4-scenario.1", "0.4-scenario.2"]
    purpose: Literal["normal_scenario_development"]
    source_group_id: Identifier
    pair_id: Identifier | None = None
    normal_scene: Literal["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "N09", "N10"]
    simulation: V03Config
    # Required even when empty: no implicit legacy control commands.
    business_schedule: Annotated[tuple[BusinessTask, ...], Field(max_length=120)]
    maintenance_windows: Annotated[tuple[MaintenanceWindow, ...], Field(max_length=120)] = ()

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.schema_version == "0.4-scenario.1":
            result.pop("maintenance_windows", None)
        return result

    def expanded_maintenance(self) -> tuple[StationMaintenance, ...]:
        return legacy_maintenance(self.simulation.protocol) + tuple(
            StationMaintenance(window.maintenance_id, station, window.start_s, window.duration_s)
            for window in self.maintenance_windows
            for station in window.station_ids
        )

    def expanded_schedule(self) -> tuple[BusinessTask, ...]:
        tasks = []
        for task in self.business_schedule:
            # Per-task deterministic stream; unrelated tasks do not shift other timings.
            seed = canonical_hash([self.simulation.protocol.seed, task.schedule_id])
            offset = random.Random(seed).randint(0, task.jitter_s)
            payload = task.model_dump(mode="json")
            payload.update(at_s=task.at_s + offset, jitter_s=0)
            tasks.append(task.model_validate(payload))
        return tuple(sorted(tasks, key=lambda task: (task.at_s, task.schedule_id)))

    @model_validator(mode="after")
    def references_and_windows(self) -> Self:
        config = self.simulation
        if config.dispatch_tasks or config.repeat_command_after_s is not None:
            raise ValueError("explicit schedule cannot contain legacy dispatch/repeat commands")
        if config.protocol.interruption is not None and not config.protocol.interruption.planned:
            raise ValueError("normal scene cannot authorize unplanned interruption")
        stations = {s.station_id: s for s in config.protocol.stations}
        if self.maintenance_windows and self.schema_version != "0.4-scenario.2":
            raise ValueError("group maintenance requires scenario contract 0.4-scenario.2")
        if self.maintenance_windows and config.protocol.interruption is not None:
            raise ValueError("explicit and legacy maintenance cannot be combined")
        if len({m.maintenance_id for m in self.maintenance_windows}) != len(
            self.maintenance_windows
        ):
            raise ValueError("maintenance IDs must be unique")
        maintained: set[str] = set()
        for maintenance in self.maintenance_windows:
            selected = set(maintenance.station_ids)
            if (
                len(selected) != len(maintenance.station_ids)
                or not selected.issubset(stations)
                or selected & maintained
            ):
                raise ValueError("unknown, duplicate or repeated maintenance station")
            maintained.update(selected)
            if maintenance.scope == "region":
                members = {
                    s for s, site in stations.items() if site.region_id == maintenance.region_id
                }
                if maintenance.region_id is None or selected != members:
                    raise ValueError(
                        "region maintenance must include exactly its configured members"
                    )
            elif maintenance.region_id is not None:
                raise ValueError("station maintenance cannot specify a region")
            if (
                maintenance.start_s < config.protocol.warmup_s
                or maintenance.start_s + maintenance.duration_s + 15 >= config.protocol.duration_s
            ):
                raise ValueError("maintenance requires warmup and complete recovery observation")
        if len({t.schedule_id for t in self.business_schedule}) != len(self.business_schedule):
            raise ValueError("schedule IDs must be unique")
        windows: dict[str, list[tuple[float, float]]] = {s: [] for s in stations}
        for task in self.business_schedule:
            if len(set(task.station_ids)) != len(task.station_ids) or not set(
                task.station_ids
            ).issubset(stations):
                raise ValueError("unknown or duplicate schedule station")
            if isinstance(task, SetpointTask) and (
                set(task.targets_kw) != set(task.station_ids)
                or any(v > stations[s].capacity_kw for s, v in task.targets_kw.items())
            ):
                raise ValueError("setpoint members or capacity bounds invalid")
            batch_span = (
                (len(task.station_ids) - 1) // task.batch_size * (3 + task.batch_interval_s)
                if isinstance(task, AllocationTask)
                else 0
            )
            end = task.at_s + task.jitter_s + batch_span + 60
            if task.at_s < config.protocol.warmup_s or end >= config.protocol.duration_s:
                raise ValueError("schedule requires warmup and complete execution observation")
            for station in task.station_ids:
                windows[station].append((task.at_s, end))
        for station_windows in windows.values():
            ordered = sorted(station_windows)
            if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:], strict=False)):
                raise ValueError("same-station schedule execution windows overlap")
        for event in self.expanded_maintenance():
            if any(
                start < event.start_s + event.duration_s + 15 and event.start_s < end
                for start, end in windows[event.station_id]
            ):
                raise ValueError("normal station tasks overlap maintenance/recovery observation")
        return self


def load_scenario(path: Path) -> ScenarioConfig:
    content = path.read_bytes()
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("scenario exceeds maximum size")
    try:
        raw = yaml.load(content.decode("utf-8"), Loader=UniqueKeyLoader)
        check_yaml_tree(raw)
        return ScenarioConfig.model_validate(raw)
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid scenario {path}: {exc}") from exc


def instantiate_scenario(template: ScenarioConfig, run_id: str) -> ScenarioConfig:
    payload = template.model_dump(mode="json")
    payload["simulation"]["protocol"]["run_id"] = run_id
    return ScenarioConfig.model_validate(payload)
