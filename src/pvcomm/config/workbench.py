"""Restricted development-template requests, never arbitrary lab instructions."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pvcomm.config.batch import BatchConfig, BatchJob, FaultJob, MatchingInputs, NormalJob, PairJob
from pvcomm.config.coverage import STRENGTHS
from pvcomm.config.fault_scenes import a01_scenario, a02_scenario, terminal_scenario
from pvcomm.config.faults import RegionScope, StationScope
from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.normal_scenes import SCENE_CODES, normal_scenario
from pvcomm.config.pairs import development_pair

FAULT_CODES = tuple(STRENGTHS)
PAIR_CODES = ("P03", "P04", "P06", "P03_recovery")
# Active development scope authorized on 2026-11-27. Historical contracts keep
# accepting 30-site records so retained runs and previous profiles remain readable.
DEVELOPMENT_SITE_COUNTS = (3, 10)


class ExperimentRequest(V01Contract):
    template: str = Field(min_length=3, max_length=20)
    site_count: Literal[3, 10, 30] = 3
    variant: Annotated[int, Field(strict=True, ge=0, le=2)] = 0
    seed: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    strength: Annotated[int, Field(strict=True, gt=0)] | None = None
    scope: Literal["station", "region"] = "station"
    station_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=30)] = ("PV01",)
    region_id: Literal["R1", "R2", "R3"] = "R1"
    direction: Literal["master_to_rtu", "rtu_to_master"] = "master_to_rtu"

    @model_validator(mode="after")
    def supported(self) -> Self:
        if self.template not in (*SCENE_CODES, *FAULT_CODES, *PAIR_CODES):
            raise ValueError("unknown development template")
        if self.template in FAULT_CODES:
            if self.strength not in STRENGTHS[self.template]:
                raise ValueError("strength must use the existing V1.3 levels")
        elif self.strength is not None:
            raise ValueError("normal and pair templates have no strength override")
        if self.template not in FAULT_CODES and (
            self.scope != "station" or self.station_ids != ("PV01",) or self.region_id != "R1"
        ):
            raise ValueError("normal and pair templates retain their declared business targets")
        if self.template != "A02" and self.direction != "master_to_rtu":
            raise ValueError("direction only applies to the A02 delay template")
        if self.template in PAIR_CODES and self.site_count != 3:
            raise ValueError("current shared-condition pair templates support three sites")
        valid = {f"PV{i + 1:02d}" for i in range(self.site_count)}
        if (
            len(set(self.station_ids)) != len(self.station_ids)
            or not set(self.station_ids) <= valid
        ):
            raise ValueError("unknown or duplicate target station")
        if self.site_count == 3 and self.template in FAULT_CODES and self.region_id == "R3":
            raise ValueError("legacy fault templates have two communication regions")
        return self


def validate_development_scope(request: ExperimentRequest, matrix: BatchConfig) -> None:
    """Guard new execution without changing historical simulation contracts."""
    if request.site_count not in DEVELOPMENT_SITE_COUNTS:
        raise ValueError("current development execution supports only 3 or 10 sites")
    for task in matrix.tasks:
        if isinstance(task, PairJob):
            simulations = [task.pair.normal.simulation, task.pair.changed.background.simulation]
        elif isinstance(task, FaultJob):
            simulations = [task.scenario.background.simulation]
        else:
            simulations = [task.scenario.simulation]
        if any(len(s.protocol.stations) != request.site_count for s in simulations):
            raise ValueError("request and matrix station counts disagree")


def requested_matrix(
    request: ExperimentRequest, identity: str, matching: MatchingInputs | None = None
) -> BatchConfig:
    """Reuse accepted templates and validate every reference after seed replacement."""
    task: BatchJob
    if request.template in SCENE_CODES:
        raw = normal_scenario(
            request.template, request.variant, site_count=request.site_count
        ).model_dump(mode="json")
        raw["simulation"]["protocol"]["seed"] = request.seed
        task = NormalJob.model_validate(dict(kind="normal", task_id="task_1", scenario=raw))
    elif request.template in FAULT_CODES:
        scope = (
            RegionScope(kind="region", region_id=request.region_id)
            if request.scope == "region"
            else StationScope(kind="station", station_ids=request.station_ids)
        )
        assert request.strength is not None
        if request.template == "A01":
            scene = a01_scenario(
                scope, request.strength, request.variant, site_count=request.site_count
            )
        elif request.template == "A02":
            scene = a02_scenario(
                scope,
                request.strength,
                request.direction,
                request.variant,
                site_count=request.site_count,
            )
        else:
            scene = terminal_scenario(
                request.template,
                scope,
                request.strength,
                request.variant,
                site_count=request.site_count,
            )
        raw = scene.model_dump(mode="json")
        raw["background"]["simulation"]["protocol"]["seed"] = request.seed
        task = FaultJob.model_validate(dict(kind="fault", task_id="task_1", scenario=raw))
    else:
        if matching is None:
            raise ValueError("pair submission requires the server's frozen matching inputs")
        raw = development_pair(request.template, identity, request.variant).model_dump(mode="json")
        raw["normal"]["simulation"]["protocol"]["seed"] = request.seed
        raw["changed"]["background"]["simulation"]["protocol"]["seed"] = request.seed
        task = PairJob.model_validate(dict(kind="pair", task_id="task_1", pair=raw))
    return BatchConfig(matrix_id=identity, matching=matching, tasks=(task,))
