"""Host-local UTC/monotonic mapping; no time scaling or device-time inference."""

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClockOrigin:
    ts_utc_ns: int
    ts_mono_ns: int
    mapping_uncertainty_ns: int

    @classmethod
    def capture(cls) -> "ClockOrigin":
        before = time.monotonic_ns()
        utc = time.time_ns()
        after = time.monotonic_ns()
        return cls(utc, (before + after) // 2, (after - before + 1) // 2)

    def elapsed_ms(self, mono_ns: int) -> int:
        if mono_ns < self.ts_mono_ns:
            raise ValueError("monotonic timestamp precedes origin")
        return (mono_ns - self.ts_mono_ns) // 1_000_000

    def utc_ns(self, mono_ns: int) -> int:
        return self.ts_utc_ns + mono_ns - self.ts_mono_ns


def in_interval(timestamp: int, start: int, end: int) -> bool:
    """Left-closed, right-open; empty intervals contain no events."""
    if end < start:
        raise ValueError("end precedes start")
    return start <= timestamp < end


class SceneClock:
    """One published scene barrier. Startup holds weather at t=0 (real protocol 1x)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.start_mono_ns: int | None = None

    def elapsed(self, mono_ns: int) -> float:
        if self.start_mono_ns is None and self.path.exists():
            with self.path.open("rb") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError("invalid scene clock record size")
            self.start_mono_ns = int(json.loads(raw)["start_mono_ns"])
        return max(0, mono_ns - self.start_mono_ns) / 1e9 if self.start_mono_ns else 0.0
