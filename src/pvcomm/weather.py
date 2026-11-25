"""Validated historical hourly source, never random or silently filled weather."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pvcomm.config.power import WeatherConfig

PARAMETERS = {
    "ALLSKY_SFC_SW_DWN": ("ghi_w_m2", "Wh/m^2", 0, 1500),
    "ALLSKY_SFC_SW_DNI": ("dni_w_m2", "Wh/m^2", 0, 1500),
    "ALLSKY_SFC_SW_DIFF": ("dhi_w_m2", "Wh/m^2", 0, 1500),
    "T2M": ("temp_air_c", "C", -80, 65),
    "WS10M": ("wind_speed_m_s", "m/s", 0, 100),
}


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


class HistoricalWeather:
    def __init__(self, path: Path, config: WeatherConfig) -> None:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != config.sha256:
            raise ValueError("historical weather SHA256 mismatch")
        raw = json.loads(content)
        if raw["header"]["time_standard"] != "UTC":
            raise ValueError("weather must use UTC")
        if raw["header"]["api"]["version"] != "v2.10.5":
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
