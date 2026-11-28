"""Finite acceptance queues, composed from existing validated scenario contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.pairs import PairConfig, development_pair
from pvcomm.config.scenes import ScenarioConfig
from pvcomm.config.similarity import CalibratedSimilarity, SimilarityPolicy
from pvcomm.storage.retention import RetentionPolicy


class Job(V01Contract):
    task_id: Identifier
    max_attempts: Annotated[int, Field(strict=True, ge=1, le=3)] = 1


class NormalJob(Job):
    kind: Literal["normal"]
    scenario: ScenarioConfig


class FaultJob(Job):
    kind: Literal["fault"]
    scenario: FaultScenarioConfig


class PairJob(Job):
    kind: Literal["pair"]
    pair: PairConfig


BatchJob = Annotated[NormalJob | FaultJob | PairJob, Field(discriminator="kind")]


class MatchingInputs(V01Contract):
    policy: SimilarityPolicy
    calibration: CalibratedSimilarity

    @model_validator(mode="after")
    def consistent(self) -> Self:
        from pvcomm.config.scenes import canonical_hash

        if self.calibration.policy_sha256 != canonical_hash(self.policy.model_dump(mode="json")):
            raise ValueError("calibration and policy fingerprints differ")
        return self


class BatchConfig(V01Contract):
    schema_version: Literal["0.4-batch.1"] = "0.4-batch.1"
    matrix_id: Identifier
    purpose: Literal["development_acceptance"] = "development_acceptance"
    # Serial first; parallel throughput is a separate measured gate.
    maximum_concurrency: Literal[1] = 1
    minimum_free_bytes: Annotated[int, Field(strict=True, ge=1024**3)] = 10 * 1024**3
    maximum_output_bytes: Annotated[int, Field(strict=True, ge=1024**3)] = 50 * 1024**3
    retention: RetentionPolicy = Field(
        default_factory=lambda: RetentionPolicy(
            schema_version="0.4-retention.1", level="full_evidence"
        )
    )
    matching: MatchingInputs | None = None
    tasks: Annotated[tuple[BatchJob, ...], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def identity_and_matching(self) -> Self:
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("batch task identities must be unique")
        pairs = [task.pair.pair_id for task in self.tasks if isinstance(task, PairJob)]
        if len(set(pairs)) != len(pairs):
            raise ValueError("batch pair templates need distinct identities")
        if pairs and self.matching is None:
            raise ValueError("pair evaluation requires thresholds frozen before batch execution")
        return self


def independent_pair_templates() -> tuple[PairConfig, ...]:
    """Twelve predeclared trials, independent of normal calibration dates/seeds."""
    result = []
    for index, code in enumerate(("P03", "P04", "P06", "P03_recovery")):
        for variant in (0, 1, 2):
            pair = development_pair(code, f"trial_{code}_{variant}", variant)
            raw = pair.model_dump(mode="json")
            for scene in (raw["normal"], raw["changed"]["background"]):
                simulation = scene["simulation"]
                simulation["protocol"]["seed"] = 6000 + index * 10 + variant
                for station in simulation["protocol"]["stations"]:
                    station["access_link"]["seed"] += 4000 + index * 100
                for region in simulation["regions"]:
                    region["uplink"]["seed"] += 4000 + index * 100
            result.append(PairConfig.model_validate(raw))
    return tuple(result)
