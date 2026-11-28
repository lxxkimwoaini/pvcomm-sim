"""Synthetic control/ASDU fixtures only; real acceptance lives under tests/lab."""

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, StationScope, TerminalFault
from pvcomm.protocol.c104_runtime import C104StationEndpoint
from pvcomm.protocol.terminal_faults import TerminalFaultRuntime
from pvcomm.qc.terminal import audit_terminal_effects, measurement_ioas

SCOPE = StationScope(kind="station", station_ids=("PV01",))


@pytest.mark.parametrize(
    "code,strength",
    [
        (a, n)
        for a, values in (
            ("A04", (2, 5, 12)),
            ("A05", (2, 5, 15)),
            ("A06", (10, 30, 60)),
            ("A08", (1, 3, 5)),
        )
        for n in values
    ],
)
def test_terminal_presets_and_strict_strength(code: str, strength: int) -> None:
    config = terminal_scenario(code, SCOPE, strength)
    assert FaultScenarioConfig.model_validate_json(config.model_dump_json()) == config
    assert config.target_stations() == ("PV01",)
    raw = config.model_dump(mode="json")
    field = {
        "A04": "missing_cycles",
        "A05": "response_delay_s",
        "A06": "feedback_duration_s",
        "A08": "recovery_cycles",
    }[code]
    raw["fault"][field] = float(strength)
    with pytest.raises(ValueError, match="integer"):
        FaultScenarioConfig.model_validate(raw)


def test_delayed_negative_confirmation_rejected_before_resource_creation() -> None:
    config = terminal_scenario("A05", SCOPE, 15)
    raw = config.model_dump(mode="json")
    raw["background"]["simulation"]["device_operations"] = [
        dict(station_id="PV01", at_s=10, enabled=False)
    ]
    with pytest.raises(ValueError, match="delayed negative confirmation is unsupported"):
        FaultScenarioConfig.model_validate(raw)
    # P06 is valid: the same disabled device is re-enabled before deferred processing.
    raw["background"]["simulation"]["device_operations"].append(
        dict(station_id="PV01", at_s=39, enabled=True)
    )
    assert FaultScenarioConfig.model_validate(raw)


@pytest.mark.parametrize("code,strength", (("A04", 5), ("A06", 30)))
def test_reporting_semantics_and_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, strength: int
) -> None:
    config = terminal_scenario(code, SCOPE, strength)
    assert isinstance(config.fault, TerminalFault)
    (tmp_path / "truth/fault").mkdir(parents=True)
    (tmp_path / "scene.json").write_text(json.dumps({"start_mono_ns": 1}))
    now = [20 * 10**9 + 1]
    monkeypatch.setattr("pvcomm.protocol.terminal_faults.time.monotonic_ns", lambda: now[0])
    events = []
    runtime = TerminalFaultRuntime(
        tmp_path,
        "PV01",
        config.fault,
        2,
        lambda name, row: events.append((name, row)),
        threading.Event(),
    )
    runtime.bind(lambda mask: sorted({1001, 1002, 1003, 1004, 1006, 1011, 2001, 3001} - mask))
    runtime.step()
    if code == "A04":
        assert {1001, 1002, 1003}.issubset(runtime.suppressed)
        assert {1004, 1011}.issubset(runtime.suppressed)
        assert 1006 not in runtime.suppressed
    else:
        assert not runtime.suppressed
        now[0] += 5 * 10**9
        runtime.accepted()
        assert runtime.suppressed == {1001, 1006}
        runtime.accepted()  # Repeated commands cannot silently extend the first fault hold.
    now[0] += strength * 2 * 10**9
    runtime.step()
    assert runtime.finished and not runtime.suppressed
    state = json.loads((tmp_path / "truth/fault/PV01.state.json").read_bytes())
    assert state["phase"] == "restored" and {1001, 1006}.issubset(state["registered_ioas"])


def test_cross_event_restore_is_rejected(tmp_path: Path) -> None:
    config = terminal_scenario("A04", SCOPE, 2)
    assert isinstance(config.fault, TerminalFault)
    (tmp_path / "truth/fault").mkdir(parents=True)
    (tmp_path / "truth/fault/control.json").write_text('{"restore":true,"fault_event_id":"other"}')
    runtime = TerminalFaultRuntime(
        tmp_path, "PV01", config.fault, 2, lambda *_: None, threading.Event()
    )
    with pytest.raises(ValueError, match="restore control"):
        runtime.step()


def test_cancel_interrupts_delayed_callback(tmp_path: Path) -> None:
    config = terminal_scenario("A05", SCOPE, 15)
    assert isinstance(config.fault, TerminalFault)
    stop = threading.Event()
    stop.set()
    runtime = TerminalFaultRuntime(tmp_path, "PV01", config.fault, 2, lambda *_: None, stop)
    runtime.active = True
    with pytest.raises(InterruptedError):
        runtime.defer_response(lambda: pytest.fail("cancelled command must not execute"))


def test_delayed_business_processing_does_not_block_protocol_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = terminal_scenario("A05", SCOPE, 15)
    assert isinstance(config.fault, TerminalFault)
    events: list[tuple[str, dict[str, object]]] = []
    runtime = TerminalFaultRuntime(
        tmp_path, "PV01", config.fault, 2, lambda k, v: events.append((k, v)), threading.Event()
    )
    runtime.active = True
    monkeypatch.setattr("pvcomm.protocol.terminal_faults.time.monotonic_ns", lambda: 10**9)
    completed: list[bool] = []
    assert runtime.defer_response(lambda: completed.append(True))
    runtime._finish_pending(16 * 10**9 - 1, cancel=False)
    assert not completed
    with pytest.raises(RuntimeError, match="already pending"):
        runtime.defer_response(lambda: None)
    runtime._finish_pending(16 * 10**9, cancel=False)
    assert completed == [True]
    assert events[-1][1]["cancelled"] is False


@pytest.mark.parametrize("accepted,sent", [(True, True), (False, True), (True, False)])
def test_lazy_confirmation_never_fabricates_acceptance(accepted: bool, sent: bool) -> None:
    """Callback-only synthetic protocol fixture, never counted as c104 evidence."""
    pending: list[Any] = []
    transmissions: list[str] = []
    decisions: list[float] = []
    callbacks: list[Any] = []

    class Point:
        value = 20.0
        io_address = 3001

        def on_receive(self, callback: Any) -> None:
            callbacks.append(callback)

        def on_before_auto_transmit(self, callback: Any) -> None:
            pass

        def on_before_read(self, callback: Any) -> None:
            pass

        def transmit(self, cause: str) -> bool:
            transmissions.append(cause)
            return sent

    class ReceivedPoint:
        io_address = 3001

        @property
        def value(self) -> float:
            # Installed c104 incoming command information is immutable.
            return 20.0

        def on_receive(self, callback: Any) -> None:
            callbacks.append(callback)

        def transmit(self, cause: str) -> bool:
            transmissions.append(cause)
            return sent

    def decide(target: float, now: float) -> bool:
        decisions.append(target)
        return accepted

    endpoint: Any = C104StationEndpoint.__new__(C104StationEndpoint)
    endpoint._c104 = SimpleNamespace(
        Point=Point,
        Information=object,
        IncomingMessage=object,
        Server=object,
        ResponseState=SimpleNamespace(SUCCESS="success", FAILURE="failure", NONE="none"),
        Cot=SimpleNamespace(ACTIVATION_CON="ACT_CON", SPONTANEOUS="SPONTANEOUS"),
    )
    endpoint._lock = threading.RLock()
    endpoint._record_event = lambda *_: None
    endpoint.config = terminal_scenario("A05", SCOPE, 15).background.simulation.protocol.stations[0]
    endpoint.state = SimpleNamespace(accept=decide, accepted_setpoint_kw=20.0)

    def defer(callback: Any) -> bool:
        pending.append(callback)
        return True

    endpoint.fault = SimpleNamespace(defer_response=defer, accepted=lambda: None)
    endpoint.command_point = ReceivedPoint()
    endpoint.power_point = endpoint.reactive_point = endpoint.voltage_point = (
        endpoint.status_point
    ) = Point()
    endpoint.accepted_setpoint_point = Point()
    endpoint.extra_points = {}
    endpoint._reporting_mask = frozenset()
    endpoint.server = SimpleNamespace(on_send_raw=lambda cb: None, on_receive_raw=lambda cb: None)
    endpoint._install_callbacks()
    assert (
        callbacks[0](
            endpoint.command_point,
            object(),
            SimpleNamespace(cot=SimpleNamespace(name="ACTIVATION")),
        )
        == "none"
    )
    assert not decisions and not transmissions  # No early accept, feedback or confirmation.
    if accepted and sent:
        pending[0]()
        assert transmissions == ["SPONTANEOUS", "ACT_CON"]
    else:
        with pytest.raises(RuntimeError, match="confirmation"):
            pending[0]()
        if not accepted:
            assert not transmissions


def test_monitoring_decoder_rejects_truncated_payload() -> None:
    # Explicit synthetic unit fixture, never transmitted or treated as PCAP.
    data = bytes.fromhex("6812000000000d0103000100e903000000803f00")
    assert measurement_ioas({"type_id": 13, "data_hex": data.hex()}) == {1001}
    with pytest.raises(ValueError, match="incomplete"):
        measurement_ioas({"type_id": 13, "data_hex": data[:-1].hex()})


def test_partial_registration_failure_can_be_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake registry is solely an error-path unit fixture, never an experiment."""
    points = {
        ioa: SimpleNamespace(io_address=ioa, type=13, value=1.0)
        for ioa in (1001, 1002, 1003, 1006, 2001)
    }
    registry = dict(points)

    class Registry:
        @property
        def points(self) -> list[SimpleNamespace]:
            return list(registry.values())

        def remove_point(self, io_address: int) -> bool:
            if io_address == 1002:
                return False
            del registry[io_address]
            return True

        def add_point(self, io_address: int, **_: object) -> SimpleNamespace:
            point = SimpleNamespace(io_address=io_address, type=13, value=0.0)
            registry[io_address] = point
            return point

    endpoint = C104StationEndpoint.__new__(C104StationEndpoint)
    endpoint._lock = threading.RLock()
    endpoint._reporting_mask = frozenset()
    endpoint._recovery_feedback = set()
    endpoint.station = Registry()
    endpoint.extra_points = {}
    for attr, ioa in (
        ("power_point", 1001),
        ("reactive_point", 1002),
        ("voltage_point", 1003),
        ("accepted_setpoint_point", 1006),
        ("status_point", 2001),
    ):
        setattr(endpoint, attr, points[ioa])
    monkeypatch.setattr(endpoint, "_install_callbacks", lambda: None)
    with pytest.raises(RuntimeError, match="suspend"):
        endpoint.set_reporting_mask(frozenset((1001, 1002)))
    assert endpoint._reporting_mask == {1001}
    assert endpoint.set_reporting_mask(frozenset()) == sorted(points)


@pytest.mark.parametrize(
    "code,strength,field,value",
    [
        ("A04", 2, "duration_s", 3),
        ("A05", 2, "missing_cycles", 2),
        ("A06", 10, "duration_s", 30),
        ("A08", 1, "duration_s", 16),
        ("A04", 2, "missing_cycles", 3),
        ("A05", 2, "response_delay_s", 3),
        ("A06", 10, "feedback_duration_s", 15),
        ("A08", 1, "recovery_cycles", 2),
    ],
)
def test_invalid_terminal_contracts(code: str, strength: int, field: str, value: int) -> None:
    raw = terminal_scenario(code, SCOPE, strength).model_dump(mode="json")
    raw["fault"][field] = value
    with pytest.raises(ValueError):
        FaultScenarioConfig.model_validate(raw)


@pytest.mark.parametrize(
    "mutation", ("none", "scope", "leak", "no_recovery", "cancelled", "wrong_event")
)
def test_independent_terminal_evidence_negative_cases(mutation: str) -> None:
    """Entirely synthetic unit fixture; these bytes never become experiment PCAP."""
    config = terminal_scenario("A04", SCOPE, 2)
    all_ioas = {1001, 1002, 1003, 1004, 1005, 1006, 1008, 1009, 1010, 1011, 2001, 3001}
    mask = all_ioas - {1006, 3001}
    terminal = [
        dict(
            station_id="PV01",
            fault_event_id="event_1",
            event_id="applied",
            event_type="terminal_fault_applied",
            ts_mono_ns=20 * 10**9,
            ts_event_utc_ns=20 * 10**9,
            suppressed_ioas=sorted(mask),
            registered_ioas=[1006, 3001],
        ),
        dict(
            station_id="PV01",
            fault_event_id="event_1",
            event_id="restored",
            event_type="terminal_fault_restored",
            ts_mono_ns=24 * 10**9,
            ts_event_utc_ns=24 * 10**9,
            suppressed_ioas=[],
            registered_ioas=sorted(all_ioas),
            reason="scheduled_end",
        ),
    ]

    def measurement(at_s: int, frame: int) -> dict[str, object]:
        return dict(
            type_id=13,
            data_hex="6812000000000d0103000100e903000000803f00",
            available_at=at_s * 10**9,
            packet_index=frame,
            src_ip=config.background.simulation.protocol.stations[0].address,
        )

    rows = [measurement(18, 1), measurement(25, 3)]
    if mutation == "scope":
        terminal[0]["suppressed_ioas"] = sorted(mask - {1004})
    elif mutation == "leak":
        rows.insert(1, measurement(22, 2))
    elif mutation == "no_recovery":
        rows.pop()
    elif mutation == "cancelled":
        terminal[1]["reason"] = "finalization"
    elif mutation == "wrong_event":
        terminal[0]["fault_event_id"] = "another"
    captures = {"primary": {"apdus": rows}, "diagnostic_rtu1": {"apdus": copy.deepcopy(rows)}}
    report = audit_terminal_effects(config, terminal, [], captures)
    assert report["passed"] is (mutation == "none")
