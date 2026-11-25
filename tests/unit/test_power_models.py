"""Analytic physical tests; synthetic boundary inputs are explicitly unit fixtures."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.power import DeviceConfig, load_power_config
from pvcomm.power.device import Inverter
from pvcomm.weather import PARAMETERS, HistoricalWeather


def device() -> DeviceConfig:
    return load_power_config(Path("configs/v0.2-clear.yaml")).devices[0]


def test_inverter_ramp_capacity_acceptance_and_resource_drop() -> None:
    cfg = device()
    model = Inverter(cfg, 30.0, last_update_mono=0)
    model.advance(30.0, 1)
    assert model.actual_kw == pytest.approx(cfg.ramp_up_kw_s)
    model.advance(30.0, 10)
    assert model.actual_kw == 30
    assert model.accept(10.0)
    model.advance(30.0, 10)
    assert model.actual_kw == 30  # No time before acceptance credited to new target.
    model.advance(30.0, 11)
    assert model.actual_kw == 27
    model.advance(1.0, 11.01)
    assert model.actual_kw == 1  # Resource boundary overrides down-ramp.
    assert model.constraint_reason == "availability_or_disconnect"
    assert model.accept(30.0)  # Legitimate target can exceed current availability.
    for invalid in (float("nan"), float("inf"), -1, 31):
        assert not model.accept(invalid)
    assert model.command_count == 2
    with pytest.raises(ValueError):
        model.advance(10, 10)


def test_inverter_q_circle_and_offline_rejection() -> None:
    cfg = device().model_copy(update={"q_target_kvar": 30.0})
    model = Inverter(cfg, 30.0, last_update_mono=0)
    model.advance(30.0, 100)
    assert model.actual_kw == 30
    assert model.actual_kw**2 + model.reactive_kvar**2 == pytest.approx(cfg.rated_kva**2)
    off = Inverter(cfg.model_copy(update={"connected": False}), 30.0, actual_kw=20)
    assert not off.accept(10)
    off.advance(30, 0.1)
    assert (off.actual_kw, off.reactive_kvar) == (0, 0)


def weather_fixture(tmp_path: Path) -> tuple[Path, Any]:
    cfg = load_power_config(Path("configs/v0.2-clear.yaml")).weather.model_copy(
        update={"source_start": datetime(2024, 6, 1, 3, tzinfo=UTC)}
    )
    raw = {
        "header": {"time_standard": "UTC", "api": {"version": "v2.10.5"}},
        "geometry": {"coordinates": [113.8, 35.4, 330.63]},
        "parameters": {key: {"units": value[1]} for key, value in PARAMETERS.items()},
        "properties": {
            "parameter": {key: {"2024060103": 1.0, "2024060104": 2.0} for key in PARAMETERS}
        },
    }
    path = tmp_path / "synthetic-weather.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, cfg.model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})


def test_weather_hourly_hold_and_exclusive_source_interval(tmp_path: Path) -> None:
    path, cfg = weather_fixture(tmp_path)
    weather = HistoricalWeather(path, cfg)
    assert weather.sample(3599.99).ghi_w_m2 == 1
    assert weather.sample(3600).ghi_w_m2 == 2
    assert weather.sample(1).source_weather_ts == datetime(2024, 6, 1, 3, 0, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="cover"):
        weather.sample(7200)


@pytest.mark.parametrize("mutation", ["unit", "missing", "gap", "hash"])
def test_invalid_weather_is_not_filled_or_zeroed(tmp_path: Path, mutation: str) -> None:
    path, cfg = weather_fixture(tmp_path)
    raw = json.loads(path.read_text())
    if mutation == "unit":
        raw["parameters"]["T2M"]["units"] = "K"
    elif mutation == "missing":
        raw["properties"]["parameter"]["T2M"]["2024060103"] = -999
    elif mutation == "gap":
        for values in raw["properties"]["parameter"].values():
            values["2024060105"] = values.pop("2024060104")
    path.write_text(json.dumps(raw) + " ")
    if mutation != "hash":
        cfg = cfg.model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    with pytest.raises(ValueError):
        HistoricalWeather(path, cfg)


def test_advected_optical_layer_is_explicit_deterministic_and_bounded(tmp_path: Path) -> None:
    import math

    from pvcomm.config.power import CloudShadow

    path, cfg = weather_fixture(tmp_path)
    cfg = cfg.model_copy(
        update={
            "cloud_shadow": CloudShadow(
                model="advected_grey_optical_layer",
                optical_depth=1.5,
                half_width_m=200.0,
                speed_m_s=10.0,
                center_s=45.0,
            )
        }
    )
    weather = HistoricalWeather(path, cfg)
    assert weather.sample(25).optical_transmittance == 1
    assert weather.sample(65).optical_transmittance == 1
    center = weather.sample(45)
    assert center.source_ghi_w_m2 == 1
    assert center.ghi_w_m2 == pytest.approx(math.exp(-1.5))
    assert center.temp_air_c == 1
    assert weather.sample(45) == center
    assert all(0 < weather.sample(t).optical_transmittance <= 1 for t in range(90))


def test_power_config_rejects_mismatched_devices_and_non_utc() -> None:
    cfg = load_power_config(Path("configs/v0.2-clear.yaml"))
    raw = cfg.model_dump(mode="json")
    raw["devices"][0]["station_id"] = "unknown"
    with pytest.raises(ValueError):
        type(cfg).model_validate(raw)
    raw = cfg.model_dump(mode="json")
    raw["weather"]["source_start"] = "2024-06-01T03:00:00"
    with pytest.raises(ValueError):
        type(cfg).model_validate(raw)


@pytest.mark.power
def test_pvlib_night_and_independent_pvwatts_reference() -> None:
    from pvcomm.power.solar import SolarModel
    from pvcomm.weather import WeatherSample

    cfg = load_power_config(Path("configs/v0.2-clear.yaml"))
    model = SolarModel(cfg.devices[0], cfg.weather)
    stamp = datetime(2024, 6, 1, 4, tzinfo=UTC)
    result = model.calculate(WeatherSample(stamp, stamp, 800.0, 600.0, 200.0, 25.0, 1.0))
    device_cfg = cfg.devices[0]
    dc = (
        result["poa_w_m2"]
        / 1000
        * device_cfg.rated_kw
        * 1000
        * device_cfg.dc_ac_ratio
        * (1 + device_cfg.gamma_pdc_per_c * (result["cell_temp_c"] - 25))
        * (1 - device_cfg.dc_loss_fraction)
    )
    pdc0 = device_cfg.rated_kw * 1000 / device_cfg.eta_inv_nom
    zeta = dc / pdc0
    eta = device_cfg.eta_inv_nom / 0.9637 * (-0.0162 * zeta - 0.0059 / zeta + 0.9858)
    assert result["p_avail_kw"] == pytest.approx(min(device_cfg.rated_kw, eta * dc / 1000))
    night = datetime(2024, 6, 1, 16, tzinfo=UTC)
    assert model.calculate(WeatherSample(night, night, 0, 0, 0, 20, 1))["p_avail_kw"] == 0


@pytest.mark.power
def test_pandapower_injection_sign_and_voltage_response() -> None:
    from pvcomm.power.feeder import ThreeSiteFeeder

    cfg = load_power_config(Path("configs/v0.2-clear.yaml"))
    stations = tuple(d.station_id for d in cfg.devices)
    feeder = ThreeSiteFeeder(cfg.feeder, stations)
    zero = {s: {"p_actual_kw": 0, "q_actual_kvar": 0} for s in stations}
    low = feeder.solve(zero)
    high = feeder.solve({s: {"p_actual_kw": 50, "q_actual_kvar": 0} for s in stations})
    assert all(high[s]["voltage_pu"] > low[s]["voltage_pu"] for s in stations)
    assert all(v["voltage_kv"] == pytest.approx(v["voltage_pu"] * 0.4) for v in high.values())
    assert high[stations[0]]["current_a"] > 0
    assert float(feeder.net.res_ext_grid.p_mw.iloc[0]) < 0.01
    with pytest.raises(ValueError):
        feeder.solve({})


def test_ipc_partial_tail_never_replaces_last_complete_snapshot(tmp_path: Path) -> None:
    from pvcomm.power.runtime import append_snapshot, read_snapshot

    path = tmp_path / "synthetic-ipc.jsonl"
    assert read_snapshot(path) is None
    append_snapshot(path, {"seq": 1})
    with path.open("ab") as stream:
        stream.write(b'{"seq":')
    assert read_snapshot(path) == {"seq": 1}
    with path.open("ab") as stream:
        stream.write(b"2}\n")
    assert read_snapshot(path) == {"seq": 2}
    with path.open("ab") as stream:
        stream.write(b"corrupt\n")
    with pytest.raises(ValueError):
        read_snapshot(path)


def test_weather_replay_uses_retained_monotonic_precision(tmp_path: Path) -> None:
    from pvcomm.config.power import CloudShadow

    path, cfg = weather_fixture(tmp_path)
    cfg = cfg.model_copy(
        update={
            "cloud_shadow": CloudShadow(
                model="advected_grey_optical_layer",
                optical_depth=1.5,
                half_width_m=200.0,
                speed_m_s=10.0,
                center_s=45.0,
            )
        }
    )
    weather = HistoricalWeather(path, cfg)
    origin_ns, model_ns = 1_234_567_890, 35_483_024_679
    elapsed = (model_ns - origin_ns) / 1e9
    original = weather.sample(elapsed)
    replay = weather.sample((model_ns - origin_ns) / 1e9)
    assert replay == original
    # ISO datetime retains microseconds, not the nanoseconds used by the model.
    rounded_elapsed = (original.source_weather_ts - cfg.source_start).total_seconds()
    assert rounded_elapsed != elapsed
    assert abs(rounded_elapsed - elapsed) <= 1e-6
