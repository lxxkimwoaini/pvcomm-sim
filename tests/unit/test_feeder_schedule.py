"""Explicit synthetic IPC and solver fixtures for scheduler regression only."""

import time
from pathlib import Path
from typing import Any, cast

import pytest

from pvcomm.config.multisite import V03Config
from pvcomm.config.presets import configuration
from pvcomm.power import runtime


def test_local_device_state_change_triggers_solve_before_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = V03Config.model_validate(configuration(3, 120))
    (tmp_path / "truth/ipc").mkdir(parents=True)
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "monotonic_ns", lambda: int(now[0] * 1e9))

    class SolverFixture:
        def __init__(self, config: object) -> None:
            self.calls = 0
            self.diagnostic_rows: dict[str, Any] = {}

        def solve(self, states: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {site: {"voltage_kv": 10.0} for site in states}

    monkeypatch.setattr(runtime, "FeederNetwork", SolverFixture)
    sample = dict(
        model_mono_ns=int(now[0] * 1e9),
        command_count=0,
        running=True,
        connected=True,
        faulted=False,
        p_actual_kw=10.0,
        q_actual_kvar=0.0,
    )
    for device in cfg.devices:
        runtime.append_snapshot(tmp_path / f"truth/ipc/{device.station_id}.jsonl", sample)
    feeder = runtime.FeederRuntime(tmp_path, cfg, lambda _kind, _payload: None)
    fixture = cast(SolverFixture, feeder.model)
    assert feeder.step()
    assert fixture.calls == 1
    now[0] = 100.5
    assert feeder.step() and fixture.calls == 1
    runtime.append_snapshot(
        tmp_path / "truth/ipc/PV01.jsonl",
        {**sample, "running": False, "p_actual_kw": 0.0, "model_mono_ns": int(now[0] * 1e9)},
    )
    assert feeder.step() and fixture.calls == 2
