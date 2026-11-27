"""Frozen prerelease processing policy; unrelated to simulator fault configuration."""

from typing import Annotated, Literal

from pydantic import Field

from pvcomm.config.lab import V01Contract


class DatasetPolicy(V01Contract):
    schema_version: Literal["0.5-dataset.1"] = "0.5-dataset.1"
    window_s: Literal[60] = 60
    step_s: Literal[5] = 5
    split_seed: Annotated[int, Field(strict=True, ge=0)] = 20261127
    model_seeds: tuple[int, ...] = (7, 17, 27)
    alert_merge_s: Literal[10] = 10
    minimum_train_normal: Annotated[int, Field(strict=True, ge=10)] = 20
    training_protocol: Literal["S1_source_date_pair_groups"] = "S1_source_date_pair_groups"
