from pathlib import Path

import pytest

from pvcomm.config.lab import V01Config, load_v01_config


def test_per_endpoint_standard_ports_require_isolation() -> None:
    config = load_v01_config(Path("configs/v0.1-acceptance-30m.yaml"))
    assert [s.port for s in config.stations] == [2404] * 3
    with pytest.raises(ValueError, match="ports must be unique"):
        V01Config.model_validate({**config.model_dump(), "topology": "loopback"})


def test_interruption_keeps_recovery_observation() -> None:
    config = load_v01_config(Path("configs/v0.1-acceptance-maintenance.yaml"))
    assert config.interruption is not None
    event = config.interruption.model_dump()
    for change in ({"station_id": "unknown"}, {"start_s": 75}):
        with pytest.raises(ValueError):
            V01Config.model_validate({**config.model_dump(), "interruption": {**event, **change}})
