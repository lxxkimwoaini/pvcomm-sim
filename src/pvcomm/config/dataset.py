"""Frozen prerelease processing policy; unrelated to simulator fault configuration."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pvcomm.config.lab import V01Contract


class DatasetPolicy(V01Contract):
    schema_version: Literal["0.5-dataset.1", "0.5.1-dataset.1"] = "0.5-dataset.1"
    window_s: Literal[10, 30, 60] = 60
    step_s: Literal[1, 5] = 5
    split_seed: Annotated[int, Field(strict=True, ge=0)] = 20261127
    model_seeds: tuple[int, ...] = (7, 17, 27)
    alert_merge_s: Literal[10] = 10
    minimum_train_normal: Annotated[int, Field(strict=True, ge=10)] = 20
    training_protocol: Literal["S1_source_date_pair_groups", "S1_stratified_development"] = (
        "S1_source_date_pair_groups"
    )

    @model_validator(mode="after")
    def fixed_scales(self) -> Self:
        if self.schema_version == "0.5-dataset.1":
            if (self.window_s, self.step_s, self.training_protocol) != (
                60,
                5,
                "S1_source_date_pair_groups",
            ):
                raise ValueError("legacy policy requires W60 and original grouping")
        elif (self.window_s, self.step_s) not in ((10, 1), (30, 1), (60, 5)) or (
            self.training_protocol != "S1_stratified_development"
        ):
            raise ValueError("V0.5.1 requires registered scale and stratified development")
        return self

    def for_scale(self, name: str) -> Self:
        scales = {"W10": (10, 1), "W30": (30, 1), "W60": (60, 5)}
        if name not in scales or self.schema_version != "0.5.1-dataset.1":
            raise ValueError("scale requires V0.5.1 W10/W30/W60")
        window, step = scales[name]
        return self.model_validate(dict(self.model_dump(), window_s=window, step_s=step))
