"""Extensible offline historical input contract; legacy WeatherConfig is frozen."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.power import CloudShadow


class HistoricalInput(V01Contract):
    schema_version: Literal["0.4-weather-source.1"]
    source_id: Identifier
    adapter: Literal["nasa_power_hourly_v1"]
    product_version: Annotated[str, Field(min_length=1)]
    source_kind: Literal["historical_satellite_reanalysis_not_field_measurement"]
    usage_basis: Annotated[str, Field(min_length=10)]
    policy_url: HttpUrl
    product_url: HttpUrl
    retrieved_at: datetime
    directory: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_start: datetime
    coverage_start: datetime
    coverage_end_exclusive: datetime
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    altitude_m: float
    sampling: Literal["hourly_mean_zero_order_hold"]
    cloud_shadow: CloudShadow | None = None

    @model_validator(mode="after")
    def utc_and_coverage(self) -> Self:
        for stamp in (
            self.retrieved_at,
            self.source_start,
            self.coverage_start,
            self.coverage_end_exclusive,
        ):
            if stamp.tzinfo is None or stamp.utcoffset() != UTC.utcoffset(None):
                raise ValueError("historical source times must explicitly use UTC")
        if not self.coverage_start <= self.source_start < self.coverage_end_exclusive:
            raise ValueError("source_start is outside historical coverage")
        if any(
            t.minute or t.second or t.microsecond
            for t in (self.coverage_start, self.coverage_end_exclusive)
        ):
            raise ValueError("hourly coverage must use complete UTC hour boundaries")
        return self
