"""Explicit V0.4 business schedule, composed with the frozen V0.3 contract."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import Field, model_validator

from pvcomm.config.lab import Identifier, V01Contract
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
    schema_version: Literal["0.4-scenario.1"]
    purpose: Literal["normal_scenario_development"]
    source_group_id: Identifier
    pair_id: Identifier | None = None
    normal_scene: Literal["N01", "N02", "N03", "N04", "N05", "N06", "N07", "N08", "N09", "N10"]
    simulation: V03Config
    # Required even when empty: no implicit legacy control commands.
    business_schedule: Annotated[tuple[BusinessTask, ...], Field(max_length=120)]

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
        maintenance = config.protocol.interruption
        if maintenance is not None and any(
            start < maintenance.start_s + maintenance.duration_s + 15 and maintenance.start_s < end
            for start, end in windows[maintenance.station_id]
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
