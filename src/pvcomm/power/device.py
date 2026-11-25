"""Deterministic constrained inverter state; callers provide physical availability."""

import math
from dataclasses import dataclass

from pvcomm.config.power import DeviceConfig


@dataclass
class Inverter:
    config: DeviceConfig
    accepted_setpoint_kw: float
    actual_kw: float = 0.0
    reactive_kvar: float = 0.0
    last_update_mono: float = 0.0
    command_count: int = 0
    constraint_reason: str = "initial"

    @property
    def running(self) -> bool:
        return self.config.enabled and self.config.connected and not self.config.faulted

    def advance(self, available_kw: float, now: float) -> None:
        if not math.isfinite(now) or now < self.last_update_mono:
            raise ValueError("device time cannot go backwards or be nonfinite")
        if not math.isfinite(available_kw) or not 0 <= available_kw <= self.config.rated_kw:
            raise ValueError("invalid physical availability")
        dt = now - self.last_update_mono
        target = min(available_kw, self.accepted_setpoint_kw) if self.running else 0.0
        delta = max(
            -self.config.ramp_down_kw_s * dt,
            min(self.config.ramp_up_kw_s * dt, target - self.actual_kw),
        )
        proposed = self.actual_kw + delta
        hard_limit = available_kw if self.running else 0.0
        self.constraint_reason = "availability_or_disconnect" if proposed > hard_limit else "ramp"
        self.actual_kw = min(proposed, hard_limit)
        q_cap = math.sqrt(max(0.0, self.config.rated_kva**2 - self.actual_kw**2))
        q_target = self.config.q_target_kvar if self.running else 0.0
        self.reactive_kvar += max(
            -self.config.q_ramp_kvar_s * dt,
            min(self.config.q_ramp_kvar_s * dt, q_target - self.reactive_kvar),
        )
        self.reactive_kvar = max(-q_cap, min(q_cap, self.reactive_kvar)) if self.running else 0.0
        self.last_update_mono = now

    def accept(self, target_kw: float) -> bool:
        if not math.isfinite(target_kw) or not 0 <= target_kw <= self.config.rated_kw:
            return False
        if not self.running:
            return False
        self.accepted_setpoint_kw = target_kw
        self.command_count += 1
        return True
