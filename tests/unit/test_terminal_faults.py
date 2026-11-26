"""Synthetic control/ASDU fixtures only; real acceptance lives under tests/lab."""

import json
import threading
from pathlib import Path

import pytest

from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, StationScope, TerminalFault
from pvcomm.protocol.terminal_faults import TerminalFaultRuntime
from pvcomm.qc.terminal import measurement_ioas

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
    runtime.bind(lambda mask: sorted({1001, 1002, 1003, 1006, 2001} - mask))
    runtime.step()
    if code == "A04":
        assert {1001, 1002, 1003}.issubset(runtime.suppressed)
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
        runtime.delay_response()


def test_monitoring_decoder_rejects_truncated_payload() -> None:
    # Explicit synthetic unit fixture, never transmitted or treated as PCAP.
    data = bytes.fromhex("6812000000000d0103000100e903000000803f00")
    assert measurement_ioas({"type_id": 13, "data_hex": data.hex()}) == {1001}
    with pytest.raises(ValueError, match="incomplete"):
        measurement_ioas({"type_id": 13, "data_hex": data[:-1].hex()})
