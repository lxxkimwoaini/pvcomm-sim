"""Validated historical hourly source, never random or silently filled weather."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pvcomm.config.multisite import V03Config
    from pvcomm.config.power import V02Config

from pvcomm.config.power import WeatherConfig
from pvcomm.config.weather import HistoricalInput

PARAMETERS = {
    "ALLSKY_SFC_SW_DWN": ("ghi_w_m2", "Wh/m^2", 0, 1500),
    "ALLSKY_SFC_SW_DNI": ("dni_w_m2", "Wh/m^2", 0, 1500),
    "ALLSKY_SFC_SW_DIFF": ("dhi_w_m2", "Wh/m^2", 0, 1500),
    "T2M": ("temp_air_c", "C", -80, 65),
    "WS10M": ("wind_speed_m_s", "m/s", 0, 100),
}


def station_weather(config: V02Config | V03Config, station_id: str) -> WeatherConfig:
    """Retain historical background identity; derive an explicit spatial model.

    Direction is propagation angle counter-clockwise from local east (x).
    Cloud center arrival is delayed by downwind distance / speed, while a
    Gaussian crosswind optical depth gives differing amplitudes.
    """
    from pvcomm.config.multisite import V03Config

    if not isinstance(config, V03Config):
        return config.weather
    background = config.weather_by_station.get(station_id, config.weather)
    if config.spatial_cloud is None or background.cloud_shadow is None:
        return background
    site = next(s for s in config.protocol.stations if s.station_id == station_id)
    angle = math.radians(config.spatial_cloud.direction_degrees)
    downwind = site.x_m * math.cos(angle) + site.y_m * math.sin(angle)
    crosswind = -site.x_m * math.sin(angle) + site.y_m * math.cos(angle)
    shadow = background.cloud_shadow.model_copy(
        update={
            "center_s": background.cloud_shadow.center_s
            + downwind / background.cloud_shadow.speed_m_s,
            "optical_depth": background.cloud_shadow.optical_depth
            * math.exp(-0.5 * (crosswind / config.spatial_cloud.crosswind_width_m) ** 2),
        }
    )
    return background.model_copy(update={"cloud_shadow": shadow})


@dataclass(frozen=True)
class WeatherSample:
    source_weather_ts: datetime
    source_interval_start: datetime
    ghi_w_m2: float
    dni_w_m2: float
    dhi_w_m2: float
    temp_air_c: float
    wind_speed_m_s: float
    source_ghi_w_m2: float = 0.0
    optical_transmittance: float = 1.0


class WeatherProvider(Protocol):
    """Offline physical driver; independent observed M3 weather is not implied."""

    def sample(self, elapsed_s: float) -> WeatherSample: ...


def open_historical_input(config: HistoricalInput) -> WeatherProvider:
    """Validate offline provenance before exposing a new source to a model.

    Policy URLs and usage basis are retained attribution, not automatic legal
    approval. Only an explicitly implemented/versioned adapter is accepted.
    """
    root = Path(config.directory)
    if (root / "source.json").is_symlink():
        raise ValueError("historical provenance must be a retained regular file")
    metadata = json.loads((root / "source.json").read_bytes())
    if (
        metadata["source_id"] != config.source_id
        or metadata["canonical_sha256"] != config.sha256
        or metadata["source_kind"] != config.source_kind
        or metadata["usage_basis"] != config.usage_basis
        or datetime.fromisoformat(metadata["retrieved_at"]) != config.retrieved_at
    ):
        raise ValueError("historical source provenance descriptor mismatch")
    files = metadata["files"]
    if not {"raw.json", "api.html", "referencing.html", "policy.html"} <= set(files):
        raise ValueError("historical provenance is missing required source or policy snapshots")
    if files["api.html"]["url"] != str(config.product_url) or files["policy.html"]["url"] != str(
        config.policy_url
    ):
        raise ValueError("historical policy/product URL differs from retained snapshot")
    for name, entry in metadata["files"].items():
        if Path(name).name != name or name in (".", "..") or "\\" in name:
            raise ValueError("unsafe historical source manifest path")
        if (root / name).is_symlink() or not (root / name).is_file():
            raise ValueError("historical source manifest requires retained regular files")
        with (root / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                raise ValueError("historical source file hash mismatch")
    raw = json.loads((root / "raw.json").read_bytes())
    canonical = json.dumps(
        {k: v for k, v in raw.items() if k != "times"}, sort_keys=True, separators=(",", ":")
    ).encode()
    if hashlib.sha256(canonical).hexdigest() != config.sha256:
        raise ValueError("historical normalized source differs from retained original")
    if (root / "weather.json").is_symlink():
        raise ValueError("normalized historical source must be a retained regular file")
    return HistoricalWeather(root / "weather.json", config)


class HistoricalWeather:
    def __init__(self, path: Path, config: WeatherConfig | HistoricalInput) -> None:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != config.sha256:
            raise ValueError("historical weather SHA256 mismatch")
        raw = json.loads(content)
        if raw["header"]["time_standard"] != "UTC":
            raise ValueError("weather must use UTC")
        expected_version = (
            config.product_version if isinstance(config, HistoricalInput) else "v2.10.5"
        )
        if raw["header"]["api"]["version"] != expected_version:
            raise ValueError("unverified weather API version")
        if raw["geometry"]["coordinates"] != [config.longitude, config.latitude, config.altitude_m]:
            raise ValueError("weather coordinate mismatch")
        self.rows: dict[datetime, dict[str, float]] = {}
        times: set[str] | None = None
        for parameter, (field, unit, low, high) in PARAMETERS.items():
            if raw["parameters"][parameter]["units"] != unit:
                raise ValueError(f"unexpected unit for {parameter}")
            values = raw["properties"]["parameter"][parameter]
            if times is not None and set(values) != times:
                raise ValueError("weather fields have different time coverage")
            times = set(values)
            for key, value in values.items():
                if isinstance(value, bool) or not isinstance(value, (float, int)):
                    raise ValueError("invalid weather scalar")
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"missing or out-of-range weather: {key}/{parameter}")
                stamp = datetime.strptime(key, "%Y%m%d%H").replace(tzinfo=UTC)
                self.rows.setdefault(stamp, {})[field] = float(value)
        ordered = sorted(self.rows)
        if not ordered or any(
            b - a != timedelta(hours=1) for a, b in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("weather is empty or discontinuous")
        if isinstance(config, HistoricalInput) and (
            ordered[0] != config.coverage_start
            or ordered[-1] + timedelta(hours=1) != config.coverage_end_exclusive
        ):
            raise ValueError("declared historical coverage does not match retained source")
        self.header: dict[str, Any] = raw["header"]
        self.config = config

    def sample(self, elapsed_s: float) -> WeatherSample:
        if not math.isfinite(elapsed_s) or elapsed_s < 0:
            raise ValueError("invalid weather elapsed time")
        timestamp = self.config.source_start + timedelta(seconds=elapsed_s)
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        if hour not in self.rows:
            raise ValueError("weather interval does not cover simulation time")
        values = dict(self.rows[hour])
        transmission = 1.0
        shadow = self.config.cloud_shadow
        if shadow is not None:
            position = shadow.speed_m_s * (elapsed_s - shadow.center_s)
            if abs(position) < shadow.half_width_m:
                # A finite advected grey optical layer: Beer-Lambert I/I0=e^-tau.
                # Smooth cosine-squared optical thickness, no random electrical data.
                tau = (
                    shadow.optical_depth
                    * math.cos(math.pi * position / (2 * shadow.half_width_m)) ** 2
                )
                transmission = math.exp(-tau)
            for field in ("ghi_w_m2", "dni_w_m2", "dhi_w_m2"):
                values[field] *= transmission
        return WeatherSample(
            timestamp,
            hour,
            **values,
            source_ghi_w_m2=self.rows[hour]["ghi_w_m2"],
            optical_transmittance=transmission,
        )
