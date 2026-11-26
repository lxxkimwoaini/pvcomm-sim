"""Predeclared traffic matching rules; independent calibration precedes evaluation."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pvcomm.config.lab import V01Contract


class SimilarityPolicy(V01Contract):
    schema_version: Literal["0.4-similarity.1"] = "0.4-similarity.1"
    pre_event_s: Literal[5] = 5
    post_event_s: Literal[20] = 20
    capture: Literal["primary"] = "primary"
    scalar_distance: Literal["symmetric_relative_max"] = "symmetric_relative_max"
    distribution_distance: Literal["sqrt_jensen_shannon_base2"] = "sqrt_jensen_shannon_base2"
    match_strategy: Literal["all_features_all_targets_and_global"] = (
        "all_features_all_targets_and_global"
    )
    length_bounds: tuple[int, ...] = (0, 64, 128, 256, 512, 1024, 1518, 9000)
    iat_bounds_s: tuple[float, ...] = (0.0, 0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
    rate_bounds: tuple[int, ...] = (0, 1, 2, 4, 8, 16, 32, 64, 128, 256)
    burst_gap_s: Annotated[float, Field(ge=0.1, le=0.1)] = 0.1
    burst_min_packets: Literal[3] = 3
    minimum_calibration_pairs: Literal[6] = 6
    threshold_rule: Literal["min_1_max_005_125_times_calibration_max"] = (
        "min_1_max_005_125_times_calibration_max"
    )

    @model_validator(mode="after")
    def fixed_distributions(self) -> Self:
        for bounds in (self.length_bounds, self.iat_bounds_s, self.rate_bounds):
            if (
                not bounds
                or bounds[0] != 0
                or any(a >= b for a, b in zip(bounds, bounds[1:], strict=False))
            ):
                raise ValueError("histogram bounds must start at zero and strictly increase")
        return self


class CalibratedSimilarity(V01Contract):
    schema_version: Literal["0.4-similarity-calibration.1"] = "0.4-similarity-calibration.1"
    policy_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    purpose: Literal["independent_normal_repeat_calibration"] = (
        "independent_normal_repeat_calibration"
    )
    pair_ids: Annotated[tuple[str, ...], Field(min_length=6)]
    run_ids: Annotated[tuple[str, ...], Field(min_length=12)]
    source_hashes: dict[str, str]
    thresholds: dict[str, Annotated[float, Field(ge=0.05, le=1)]]

    @model_validator(mode="after")
    def independent_records(self) -> Self:
        if len(set(self.pair_ids)) != len(self.pair_ids) or len(set(self.run_ids)) != len(
            self.run_ids
        ):
            raise ValueError("calibration needs distinct independent runs and pair identities")
        if (
            len(self.run_ids) != 2 * len(self.pair_ids)
            or not self.thresholds
            or not self.source_hashes
        ):
            raise ValueError("incomplete calibration evidence")
        return self
