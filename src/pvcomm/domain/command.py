"""Application command facts, independent of the protocol/model implementation."""

from dataclasses import dataclass
from typing import Literal

CommandOutcome = Literal[
    "accepted",
    "negative_confirmation",
    "confirmation_timeout",
    "connection_unavailable",
    "connection_lost",
    "session_quarantined",
    "ambiguous_confirmation",
]


@dataclass(frozen=True)
class CommandResult:
    task_id: str
    station_id: str
    outcome: CommandOutcome
    positive_confirmation: bool | None
    stack_result: bool | None
    connection_epoch: int


class ObservationUnavailable(RuntimeError):
    """Expected missing/stale/invalid observation, never an Oracle fallback."""

    def __init__(self, station_id: str, reason: str) -> None:
        self.station_id = station_id
        self.reason = reason
        super().__init__(f"{reason} observed availability for {station_id}")
