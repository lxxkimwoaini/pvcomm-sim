"""Bounded Oracle-only RTU fault state. Never loaded by the master."""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pvcomm.clock import SceneClock
from pvcomm.config.faults import TerminalFault


class TerminalFaultRuntime:
    def __init__(
        self,
        root: Path,
        station_id: str,
        fault: TerminalFault,
        period_s: int,
        record: Callable[[str, dict[str, object]], None],
        stop: threading.Event,
    ) -> None:
        self.root, self.station_id, self.fault = root, station_id, fault
        self.period_s, self.record, self.stop = period_s, record, stop
        self.clock = SceneClock(root / "scene.json")
        self.active = self.finished = self.triggered = False
        self.suppressed: frozenset[int] = frozenset()
        self._end_ns = 0
        self._control_stamp: int | None = None
        self._force = False
        self._mask: Callable[[frozenset[int]], list[int]] | None = None
        self._state: dict[str, Any] = {}
        self._measurement_ioas: frozenset[int] = frozenset()
        self._pending_lock = threading.Lock()
        self._pending: tuple[int, int, Callable[[], None]] | None = None

    def bind(self, mask: Callable[[frozenset[int]], list[int]]) -> None:
        self._mask = mask
        self._measurement_ioas = frozenset(mask(frozenset())) - {3001}

    def _publish(self, phase: str, **details: Any) -> None:
        assert self._mask is not None
        registered = self._mask(self.suppressed)
        verified_ns = time.monotonic_ns()
        hold_for_s = details.pop("hold_for_s", None)
        if hold_for_s is not None:
            self._end_ns = verified_ns + int(hold_for_s) * 10**9
            details["end_mono_ns"] = self._end_ns
        self._state = dict(
            station_id=self.station_id,
            fault_event_id=self.fault.event_id,
            phase=phase,
            ts_mono_ns=verified_ns,
            suppressed_ioas=sorted(self.suppressed),
            registered_ioas=registered,
            **details,
        )
        self.record("terminal_fault_" + phase, self._state)
        path = self.root / f"truth/fault/{self.station_id}.state.json"
        stage = path.with_suffix(".tmp")
        stage.write_text(json.dumps(self._state) + "\n", encoding="utf-8")
        stage.replace(path)

    def step(self) -> None:
        if self.finished:
            self._finish_pending(time.monotonic_ns(), cancel=self.stop.is_set() or self._force)
            return
        control = self.root / "truth/fault/control.json"
        if control.exists():
            stamp = control.stat().st_mtime_ns
            if stamp != self._control_stamp:
                value = json.loads(control.read_bytes())
                if value != {"restore": True, "fault_event_id": self.fault.event_id}:
                    raise ValueError("invalid bounded terminal restore control")
                self._force, self._control_stamp = True, stamp
        now = time.monotonic_ns()
        if self._force or self.stop.is_set():
            self._finish_pending(now, cancel=True)
        else:
            self._finish_pending(now, cancel=False)
        if self.active and (self._force or now >= self._end_ns):
            self.restore("finalization" if self._force else "scheduled_end")
        elif not self.active and not self._force and self.clock.elapsed(now) >= self.fault.start_s:
            self.active = True
            duration = (
                self.period_s * int(self.fault.missing_cycles or 0)
                if self.fault.fault_type == "A04"
                else self.fault.duration_s
            )
            self._end_ns = now + duration * 10**9
            if self.fault.fault_type in ("A04", "A08"):
                # A04 keeps command acknowledgements and the explicit accepted target.
                # A08 includes the accepted-target point in the unavailable business state.
                self.suppressed = self._measurement_ioas
                if self.fault.fault_type == "A04":
                    self.suppressed -= {1006}
            self._publish("applied", hold_for_s=duration)

    def accepted(self) -> None:
        if self.active and self.fault.fault_type == "A06" and not self.triggered:
            self.triggered = True
            self.suppressed = frozenset((1001, 1006))
            self._end_ns = time.monotonic_ns() + int(self.fault.feedback_duration_s or 0) * 10**9
            self._publish("feedback_hold", hold_for_s=self.fault.feedback_duration_s)

    def defer_response(self, complete: Callable[[], None]) -> bool:
        """Leave lib60870's reader running while the business processor waits."""
        if not self.active or self.fault.fault_type != "A05":
            return False
        if self.stop.is_set():
            raise InterruptedError("terminal delayed response cancelled")
        begin = time.monotonic_ns()
        with self._pending_lock:
            if self._pending is not None:
                raise RuntimeError("a delayed station command is already pending")
            self.record(
                "terminal_response_delay_started",
                {
                    "station_id": self.station_id,
                    "fault_event_id": self.fault.event_id,
                    "delay_s": self.fault.response_delay_s,
                    "begin_mono_ns": begin,
                },
            )
            self._pending = begin, begin + int(self.fault.response_delay_s or 0) * 10**9, complete
        return True

    def _finish_pending(self, now: int, *, cancel: bool) -> None:
        with self._pending_lock:
            pending = self._pending
            if pending is None or (not cancel and now < pending[1]):
                return
            self._pending = None
        begin, _, complete = pending
        self.record(
            "terminal_response_delay_finished",
            {
                "station_id": self.station_id,
                "fault_event_id": self.fault.event_id,
                "begin_mono_ns": begin,
                "cancelled": cancel,
            },
        )
        if not cancel:
            complete()

    def restore(self, reason: str) -> None:
        if self.finished:
            return
        if reason != "scheduled_end":
            self._finish_pending(time.monotonic_ns(), cancel=True)
        self.suppressed = frozenset()
        self.active = False
        self._publish("restored", reason=reason, triggered=self.triggered)
        self.finished = True
