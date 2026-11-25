"""Explicit test doubles for lifecycle ordering, never protocol experiment evidence."""

import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from pvcomm.config.lab import load_v01_config
from pvcomm.protocol.c104_runtime import C104Master


def test_explicit_initialization_keeps_gi_and_clock_waits_on_caller() -> None:
    endpoint = C104Master.__new__(C104Master)
    endpoint._managed_lifecycle = True
    endpoint._stations = load_v01_config(Path("configs/v0.1-lab-smoke.yaml")).stations[:1]
    endpoint._c104 = ModuleType("unit_test_double")
    endpoint._c104.__dict__["Cot"] = SimpleNamespace(ACTIVATION=6)
    calls: list[str] = []

    def interrogation(**kwargs: Any) -> bool:
        calls.append("GI")
        return True

    def clock_sync(**kwargs: Any) -> bool:
        calls.append("clock")
        return True

    endpoint._connections = {
        "PV01": SimpleNamespace(
            interrogation=interrogation,
            clock_sync=clock_sync,
        )
    }
    endpoint._record_event = lambda kind, payload: calls.append(kind)
    endpoint.interrogate_all()
    assert calls == ["GI", "master_interrogation", "clock", "master_clock_synchronization"]


def test_stop_waits_for_stopdt_and_closed_callbacks_before_client_stop() -> None:
    endpoint = C104Master.__new__(C104Master)
    endpoint._managed_lifecycle = True
    endpoint._lifecycle_condition = threading.Condition()
    endpoint._callback_states = {"PV01": "OPEN"}
    calls: list[str] = []
    connection: Any = SimpleNamespace(state=SimpleNamespace(name="OPEN"))

    def mute() -> bool:
        calls.append("STOPDT confirmation callback")
        endpoint._callback_states["PV01"] = "OPEN_MUTED"
        return True

    def disconnect() -> None:
        assert endpoint._callback_states["PV01"] == "OPEN_MUTED"
        calls.append("CLOSED callback")
        endpoint._callback_states["PV01"] = "CLOSED"

    connection.mute, connection.disconnect = mute, disconnect
    endpoint._connections = {"PV01": connection}
    endpoint.client = SimpleNamespace(stop=lambda: calls.append("client stop"))
    endpoint._record_event = lambda kind, payload: calls.append(kind)
    endpoint.stop()
    assert calls == [
        "master_quiescing",
        "STOPDT confirmation callback",
        "CLOSED callback",
        "master_quiesced",
        "client stop",
        "master_stopped",
    ]


def test_failed_quiescence_still_stops_client_without_claiming_success() -> None:
    endpoint = C104Master.__new__(C104Master)
    endpoint._managed_lifecycle = True
    calls: list[str] = []
    endpoint.client = SimpleNamespace(stop=lambda: calls.append("client stop"))
    endpoint._record_event = lambda kind, payload: calls.append(kind)

    def failed() -> None:
        raise TimeoutError("missing actual protocol confirmation")

    endpoint._quiesce_connections = failed  # type: ignore[method-assign]
    with pytest.raises(TimeoutError, match="missing actual"):
        endpoint.stop()
    assert calls == ["client stop"]
