"""Adapt RTU readbacks to the existing fault journal and finally restoration."""

import json
import time
from pathlib import Path

from pvcomm.clock import ClockOrigin
from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.network.regional import RegionalNetwork
from pvcomm.scenario.link_fault import LinkFaultController


class TerminalFaultController(LinkFaultController):
    def __init__(
        self, root: Path, config: FaultScenarioConfig, network: RegionalNetwork, origin: ClockOrigin
    ) -> None:
        super().__init__(root, config, network, origin)
        self.root = root
        self.applied: set[str] = set()
        self.restored: set[str] = set()
        self._finalizing = False

    def step(self) -> None:
        for station in self.config.target_stations():
            path = self.root / f"truth/fault/{station}.state.json"
            if not path.exists():
                continue
            state = json.loads(path.read_bytes())
            if (
                state["fault_event_id"] != self.config.fault.event_id
                or state["station_id"] != station
            ):
                raise RuntimeError("cross-task terminal fault readback")
            if state["phase"] in ("applied", "feedback_hold") and station not in self.applied:
                self._record("apply_verified", after=state)
                self.applied.add(station)
            if state["phase"] == "restored" and station not in self.restored:
                self._record("restore_verified", after=state)
                self.restored.add(station)
        targets = set(self.config.target_stations())
        if self.applied == targets and self._active_ns is None:
            self._active_ns = time.monotonic_ns()
            self._record("fault_active", target_stations=sorted(targets))
        if self.restored == targets and not self._finished:
            self._record(
                "fault_finished",
                reason="run_finalization" if self._finalizing else "scheduled_end",
                was_applied=bool(self.applied),
                recovery_censored=self._finalizing,
            )
            self._finished = True

    def restore(self, *, reason: str) -> None:
        if self._finished:
            return
        self._finalizing = True
        self.step()
        path = self.root / "truth/fault/control.json"
        stage = path.with_suffix(".tmp")
        stage.write_text(
            json.dumps({"restore": True, "fault_event_id": self.config.fault.event_id}),
            encoding="utf-8",
        )
        stage.replace(path)
        deadline = time.monotonic() + 3
        while self.applied - self.restored and time.monotonic() < deadline:
            self.step()
            time.sleep(0.05)
        if self.applied - self.restored:
            raise RuntimeError("terminal fault restoration readback missing")
        if not self._finished:
            self._record(
                "fault_finished",
                reason=reason,
                was_applied=bool(self.applied),
                recovery_censored=True,
            )
            self._finished = True
