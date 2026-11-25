"""pvlib POA/SAPM temperature/PVWatts AC, with explicit unit conversions."""

import importlib
import math

from pvcomm.config.power import DeviceConfig, WeatherConfig
from pvcomm.weather import WeatherSample


class SolarModel:
    def __init__(self, config: DeviceConfig, weather: WeatherConfig) -> None:
        self.config = config
        self.pvlib = importlib.import_module("pvlib")
        self.pandas = importlib.import_module("pandas")
        self.weather = weather

    def calculate(self, sample: WeatherSample) -> dict[str, float]:
        cfg = self.config
        stamp = self.pandas.DatetimeIndex([sample.source_weather_ts])
        position = self.pvlib.solarposition.get_solarposition(
            stamp, self.weather.latitude, self.weather.longitude, altitude=self.weather.altitude_m
        )
        zenith = float(position.apparent_zenith.iloc[0])
        poa = self.pvlib.irradiance.get_total_irradiance(
            cfg.tilt_degrees,
            cfg.azimuth_degrees,
            zenith,
            float(position.azimuth.iloc[0]),
            sample.dni_w_m2,
            sample.ghi_w_m2,
            sample.dhi_w_m2,
            model="isotropic",
        )
        irradiance = max(0.0, float(poa["poa_global"])) if zenith < 90 else 0.0
        parameters = self.pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
            "open_rack_glass_glass"
        ]
        cell_temp = float(
            self.pvlib.temperature.sapm_cell(
                irradiance, sample.temp_air_c, sample.wind_speed_m_s, **parameters
            )
        )
        dc = float(
            self.pvlib.pvsystem.pvwatts_dc(
                irradiance, cell_temp, cfg.rated_kw * 1000 * cfg.dc_ac_ratio, cfg.gamma_pdc_per_c
            )
        ) * (1 - cfg.dc_loss_fraction)
        ac = (
            float(
                self.pvlib.inverter.pvwatts(
                    max(0.0, dc), cfg.rated_kw * 1000 / cfg.eta_inv_nom, eta_inv_nom=cfg.eta_inv_nom
                )
            )
            / 1000
        )
        if not all(math.isfinite(v) for v in (irradiance, cell_temp, ac)):
            raise ValueError("pvlib produced nonfinite electrical output")
        return {
            "poa_w_m2": irradiance,
            "cell_temp_c": cell_temp,
            "p_avail_kw": min(cfg.rated_kw, max(0.0, ac)),
        }
