"""Owned A01 lifecycle. All plans and readbacks remain diagnostic evidence."""

import json
import time
from pathlib import Path
from typing import Any

from pvcomm.clock import ClockOrigin, SceneClock
from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.network.regional import RegionalNetwork


class LinkFaultController:
    def __init__(
        self, root: Path, config: FaultScenarioConfig, network: RegionalNetwork, origin: ClockOrigin
    ) -> None:
        self.config = config
        self.network = network
        self.origin = origin
        self.clock = SceneClock(root / "scene.json")
        self.ports = network.interruption_ports(config.fault.scope)
        self._touched: list[tuple[str, str]] = []
        self._active_ns: int | None = None
        self._finished = False
        self._sequence = 0
        self._stream = (root / "truth/fault/events.jsonl").open("x", encoding="utf-8")

    def _record(self, event: str, **payload: Any) -> None:
        mono = time.monotonic_ns()
        self._sequence += 1
        row = {
            **payload,
            "event_id": f"fault-{self._sequence:09d}",
            "fault_event_id": self.config.fault.event_id,
            "run_id": self.config.background.simulation.protocol.run_id,
            "event_type": event,
            "ts_mono_ns": mono,
            "ts_event_utc_ns": self.origin.utc_ns(mono),
            "sim_elapsed_ms": int(self.clock.elapsed(mono) * 1000),
        }
        self._stream.write(json.dumps(row, allow_nan=False) + "\n")
        self._stream.flush()

    def step(self) -> None:
        now = time.monotonic_ns()
        if self._finished or self.clock.start_mono_ns is None and not self.clock.path.exists():
            return
        if self._active_ns is None and self.clock.elapsed(now) >= self.config.fault.start_s:
            for namespace, interface in self.ports:
                before = self.network.bridge_port_evidence(namespace, interface)
                if before["state"] != "forwarding":
                    raise RuntimeError("A01 requires a verified forwarding baseline")
                self._record("apply_requested", before=before)
                # Include a possibly partially successful operation in finally restoration.
                self._touched.append((namespace, interface))
                try:
                    after = self.network.set_bridge_forwarding(namespace, interface, enabled=False)
                except Exception as exc:
                    self._record("apply_failed", before=before, error=str(exc))
                    raise
                self._record("apply_verified", before=before, after=after)
            self._record("fault_active", target_stations=self.config.target_stations())
            self._active_ns = time.monotonic_ns()
        elif self._active_ns is not None and (
            now - self._active_ns >= self.config.fault.duration_s * 1_000_000_000
        ):
            self.restore(reason="scheduled_end")

    def restore(self, *, reason: str) -> None:
        failures = []
        for namespace, interface in self._touched.copy()[::-1]:
            try:
                before = self.network.bridge_port_evidence(namespace, interface)
                self._record("restore_requested", reason=reason, before=before)
                after = self.network.set_bridge_forwarding(namespace, interface, enabled=True)
                self._record("restore_verified", reason=reason, before=before, after=after)
                self._touched.remove((namespace, interface))
            except Exception as exc:
                failures.append(f"{namespace}/{interface}: {exc}")
                self._record("restore_failed", reason=reason, error=str(exc))
        if failures:
            raise RuntimeError("; ".join(failures))
        if not self._finished:
            self._record("fault_finished", reason=reason, was_applied=self._active_ns is not None)
        self._finished = True

    def close(self) -> None:
        try:
            self.restore(reason="run_finalization")
        finally:
            self._stream.close()
