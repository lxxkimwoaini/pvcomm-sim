import io
import json

import pytest

from pvcomm.clock import ClockOrigin, in_interval
from pvcomm.logging import create_logger


def test_clock_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([100, 110])
    monkeypatch.setattr("pvcomm.clock.time.monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr("pvcomm.clock.time.time_ns", lambda: 1000)
    origin = ClockOrigin.capture()
    assert origin.ts_mono_ns == 105
    assert origin.mapping_uncertainty_ns == 5
    assert origin.utc_ns(205) == 1100
    assert origin.elapsed_ms(2_000_105) == 2
    with pytest.raises(ValueError):
        origin.elapsed_ms(100)


def test_interval() -> None:
    assert in_interval(10, 10, 20)
    assert not in_interval(20, 10, 20)
    assert not in_interval(10, 10, 10)
    with pytest.raises(ValueError):
        in_interval(10, 20, 10)


def test_structured_error_log() -> None:
    output = io.StringIO()
    logger = create_logger("pvcomm.test", output)
    try:
        raise ValueError("test error")
    except ValueError:
        logger.exception("failed", extra={"run_id": "test_run", "event": "test_failed"})
    row = json.loads(output.getvalue())
    assert row["run_id"] == "test_run"
    assert row["module"] == "pvcomm.test"
    assert row["event"] == "test_failed"
    assert row["ts_event"].endswith("+00:00")
    assert row["sim_elapsed_ms"] is None
    assert "ValueError: test error" in row["error"]
