"""A02 uses the same Oracle journal and owned-resource lifecycle as A01."""

import time
from pathlib import Path
from typing import Any

from pvcomm.clock import ClockOrigin
from pvcomm.config.faults import FaultScenarioConfig, LinkDelay
from pvcomm.network.regional import RegionalNetwork
from pvcomm.scenario.link_fault import LinkFaultController


class DelayFaultController(LinkFaultController):
    def __init__(
        self, root: Path, config: FaultScenarioConfig, network: RegionalNetwork, origin: ClockOrigin
    ) -> None:
        if not isinstance(config.fault, LinkDelay):
            raise ValueError("delay controller requires A02")
        super().__init__(root, config, network, origin)
        self.delay = config.fault
        self.ports = network.delay_ports(self.delay.scope, self.delay.direction)
        self.baselines: dict[tuple[str, str], dict[str, Any]] = {}
        self._restored_ns: int | None = None

    def step(self) -> None:
        now = time.monotonic_ns()
        if self._finished or self.clock.start_mono_ns is None and not self.clock.path.exists():
            return
        if self._active_ns is None and self.clock.elapsed(now) >= self.delay.start_s:
            # Verify *all* initial qdiscs before changing the first one.
            self.network.shaping_evidence()
            for port in self.ports:
                before = self.network.delay_evidence(*port)
                self.baselines[port] = before
                self._record("apply_requested", before=before, direction=self.delay.direction)
                self._touched.append(port)
                try:
                    after = self.network.change_delay(*port, self.delay.additional_delay_ms)
                except Exception as exc:
                    self._record("apply_failed", before=before, error=str(exc))
                    raise
                self._record("apply_verified", before=before, after=after)
            self._record("fault_active", target_stations=self.config.target_stations())
            self._active_ns = time.monotonic_ns()
        elif self._active_ns is not None and now - self._active_ns >= self.delay.duration_s * 10**9:
            if self._restored_ns is None:
                self.restore(reason="scheduled_end")
            else:
                self._check_drain()

    def _check_drain(self) -> None:
        readbacks = [self.network.delay_evidence(*port) for port in self.ports]
        if all(r["actual"]["qlen"] == 0 for r in readbacks):
            self._record("queue_drained", readbacks=readbacks)
            self._record("fault_finished", reason="scheduled_end", was_applied=True)
            self._finished = True
        elif self._restored_ns is not None and time.monotonic_ns() - self._restored_ns > 15 * 10**9:
            raise RuntimeError("restored A02 queues did not drain within 15 seconds")

    def restore(self, *, reason: str) -> None:
        failures = []
        for port in self._touched.copy()[::-1]:
            try:
                before = self.network.delay_evidence(*port)
                self._record("restore_requested", reason=reason, before=before)
                after = self.network.change_delay(*port, 0)
                if after["actual"]["options"] != self.baselines[port]["actual"]["options"]:
                    raise RuntimeError("restored A02 parameters differ from initial baseline")
                self._record("restore_verified", reason=reason, before=before, after=after)
                self._touched.remove(port)
            except Exception as exc:
                failures.append(f"{port}: {exc}")
                self._record("restore_failed", reason=reason, error=str(exc))
        if failures:
            raise RuntimeError("; ".join(failures))
        if self._finished:
            return
        if self._restored_ns is None:
            self._restored_ns = time.monotonic_ns()
        if reason == "scheduled_end":
            self._check_drain()
        else:
            # Cancellation still restores first; pending delivery is explicitly
            # censored rather than labelled as successful recovery.
            self._record(
                "fault_finished",
                reason=reason,
                was_applied=self._active_ns is not None,
                recovery_censored=True,
            )
            self._finished = True
