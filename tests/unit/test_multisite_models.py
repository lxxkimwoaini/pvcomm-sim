"""Development assumption and analytic allocation tests; no fake lab evidence."""

from pathlib import Path

import pytest

from pvcomm.business.dispatch import allocate
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import power_document
from pvcomm.config.presets import configuration
from pvcomm.weather import station_weather


@pytest.mark.parametrize("count", [3, 10, 20, 30])
def test_configuration_scales_without_duplicate_identity_or_coupled_topologies(count: int) -> None:
    cfg = V03Config.model_validate(configuration(count, 120))
    assert len(cfg.protocol.stations) == count
    assert len({s.address for s in cfg.protocol.stations}) == count
    assert len(cfg.regions) == len(cfg.feeders) == 3
    if count >= 10:
        assert all(
            len({s.feeder_id for s in cfg.protocol.stations if s.region_id == r.region_id}) == 3
            for r in cfg.regions
        )


def test_spatial_advection_has_position_delay_and_crosswind_amplitude() -> None:
    cfg = power_document(Path("configs/v0.3-prerequisite.yaml"))
    assert isinstance(cfg, V03Config)
    assert cfg.weather.cloud_shadow is not None
    first, downwind = (station_weather(cfg, s) for s in ("PV01", "PV02"))
    assert first.cloud_shadow is not None and downwind.cloud_shadow is not None
    assert downwind.cloud_shadow.center_s - first.cloud_shadow.center_s == pytest.approx(
        100 * 3**0.5 / 2 / 10
    )
    assert downwind.cloud_shadow.optical_depth < first.cloud_shadow.optical_depth
    assert first.sha256 == downwind.sha256 == cfg.weather.sha256
    assert first.source_start == downwind.source_start


def test_capacity_and_observed_resource_allocation_shortage_is_explicit() -> None:
    capacities = {"small": 30.0, "large": 100.0}
    assert allocate(65, capacities) == ({"small": 15.0, "large": 50.0}, 0.0)
    targets, shortage = allocate(65, capacities, {"small": 10.0, "large": 20.0})
    assert targets == {"small": 10.0, "large": 20.0} and shortage == 35
    assert allocate(10, capacities, {"small": 0.0, "large": 0.0}) == (
        {"small": 0.0, "large": 0.0},
        10.0,
    )
    with pytest.raises(ValueError):
        allocate(10, capacities, {"small": 10.0})
    with pytest.raises(ValueError):
        allocate(10, capacities, {"small": float("nan"), "large": 10.0})
