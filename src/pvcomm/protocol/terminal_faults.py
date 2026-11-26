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

    def bind(self, mask: Callable[[frozenset[int]], list[int]]) -> None:
        self._mask = mask

    def _publish(self, phase: str, **details: Any) -> None:
        assert self._mask is not None
        self._state = dict(
            station_id=self.station_id,
            fault_event_id=self.fault.event_id,
            phase=phase,
            ts_mono_ns=time.monotonic_ns(),
            suppressed_ioas=sorted(self.suppressed),
            registered_ioas=self._mask(self.suppressed),
            **details,
        )
        self.record("terminal_fault_" + phase, self._state)
        path = self.root / f"truth/fault/{self.station_id}.state.json"
        stage = path.with_suffix(".tmp")
        stage.write_text(json.dumps(self._state) + "\n", encoding="utf-8")
        stage.replace(path)

    def step(self) -> None:
        if self.finished:
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
                self.suppressed = frozenset((1001, 1002, 1003, 1005, 1008, 2001))
                if self.fault.fault_type == "A08":
                    self.suppressed |= {1006}
            self._publish("applied", end_mono_ns=self._end_ns)

    def accepted(self) -> None:
        if self.active and self.fault.fault_type == "A06" and not self.triggered:
            self.triggered = True
            self.suppressed = frozenset((1001, 1006))
            self._end_ns = time.monotonic_ns() + int(self.fault.feedback_duration_s or 0) * 10**9
            self._publish("feedback_hold", end_mono_ns=self._end_ns)

    def delay_response(self) -> None:
        if not self.active or self.fault.fault_type != "A05":
            return
        begin = time.monotonic_ns()
        self.record(
            "terminal_response_delay_started",
            {
                "station_id": self.station_id,
                "fault_event_id": self.fault.event_id,
                "delay_s": self.fault.response_delay_s,
                "begin_mono_ns": begin,
            },
        )
        cancelled = self.stop.wait(float(self.fault.response_delay_s or 0))
        self.record(
            "terminal_response_delay_finished",
            {
                "station_id": self.station_id,
                "fault_event_id": self.fault.event_id,
                "begin_mono_ns": begin,
                "cancelled": cancelled,
            },
        )
        if cancelled:
            raise InterruptedError("terminal delayed response cancelled")

    def restore(self, reason: str) -> None:
        if self.finished:
            return
        self.suppressed = frozenset()
        self.active, self.finished = False, True
        self._publish("restored", reason=reason, triggered=self.triggered)
