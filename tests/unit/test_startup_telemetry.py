"""Synthetic endpoint fixtures verify scheduling only; no simulated wire evidence."""

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from pvcomm.protocol.c104_runtime import C104StationEndpoint


def endpoint_fixture() -> tuple[Any, list[tuple[int, str]], list[tuple[str, dict[str, Any]]]]:
    endpoint: Any = C104StationEndpoint.__new__(C104StationEndpoint)
    transmissions: list[tuple[int, str]] = []
    events: list[tuple[str, dict[str, Any]]] = []

    def point(ioa: int, value: float | bool) -> SimpleNamespace:
        return SimpleNamespace(
            io_address=ioa,
            value=value,
            transmit=lambda cause: transmissions.append((ioa, cause)),
        )

    endpoint._lock = threading.RLock()
    endpoint._active = True
    endpoint._c104 = SimpleNamespace(Cot=SimpleNamespace(PERIODIC="periodic", SPONTANEOUS="event"))
    endpoint.config = SimpleNamespace(
        station_id="PV01", telemetry_period_s=2, capacity_kw=30, deadband_fraction=0.01
    )
    endpoint.state = SimpleNamespace(
        actual_kw=20.0, advance=lambda now: None, telemetry=lambda: {2001: True}
    )
    endpoint.power_point = point(1001, 20.0)
    endpoint.reactive_point = point(1002, 0.0)
    endpoint.voltage_point = point(1003, 0.4)
    endpoint.status_point = point(2001, True)
    endpoint.extra_points = {}
    endpoint._recovery_feedback = set()
    endpoint._reporting_mask = frozenset()
    endpoint._last_sent_power = endpoint._last_sent_running = None
    endpoint._next_report_s = 0.75
    endpoint._next_startup_report_mono = 100.75
    endpoint._logical_elapsed = lambda now: max(0, now - 110)
    endpoint._record_event = lambda name, row: events.append((name, row))
    return endpoint, transmissions, events


def test_startup_reports_continue_without_advancing_scene_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, sent, events = endpoint_fixture()
    now = [100.7]
    monkeypatch.setattr("pvcomm.protocol.c104_runtime.time.monotonic", lambda: now[0])
    endpoint.tick()
    assert not sent
    for stamp in (100.75, 102.75, 104.75, 106.75, 108.75):
        now[0] = stamp
        endpoint.tick()
    assert len(sent) == 20
    assert all(row["reason"] == "startup_periodic" for _, row in events)
    assert all(row["scene_elapsed_s"] == 0 for _, row in events)
    assert endpoint._next_report_s == 0.75
    sent.clear()
    events.clear()
    now[0] = 110.7
    endpoint.tick()
    assert not sent  # No phase-zero reset or catch-up burst at scene release.
    now[0] = 110.75
    endpoint.tick()
    assert len(sent) == 4
    assert events[0][1]["reason"] == "periodic"
    assert events[0][1]["scene_elapsed_s"] == 0.75
    assert endpoint._next_report_s == 2.75


@pytest.mark.parametrize("active,mask", [(False, frozenset()), (True, frozenset({1001, 1003}))])
def test_startup_cannot_bypass_inactive_endpoint_or_reporting_mask(
    monkeypatch: pytest.MonkeyPatch, active: bool, mask: frozenset[int]
) -> None:
    endpoint, sent, events = endpoint_fixture()
    endpoint._active, endpoint._reporting_mask = active, mask
    monkeypatch.setattr("pvcomm.protocol.c104_runtime.time.monotonic", lambda: 105.0)
    endpoint.tick()
    assert {ioa for ioa, _ in sent} == ({1002, 2001} if active else set())
    if active:
        assert {row["ioa"] for name, row in events if name == "terminal_report_suppressed"} == mask
    else:
        assert not events


def test_legacy_automatic_reporting_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint, sent, events = endpoint_fixture()
    endpoint._logical_elapsed = None
    monkeypatch.setattr("pvcomm.protocol.c104_runtime.time.monotonic", lambda: 105.0)
    endpoint.tick()
    assert not sent and not events
