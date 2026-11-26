"""Read-only matching of actual c104 raw ACT_CON observations.

Only the installed single-object C_SE_NC_1 profile is recognized. This decoder
never constructs or transmits packets. A timeout quarantines the station until
a genuinely closed/reopened session, since IEC 104 carries no application ID.
"""

import math
import struct
import threading
import time
from dataclasses import dataclass

from pvcomm.domain.command import CommandOutcome


@dataclass(frozen=True)
class Confirmation:
    common_address: int
    ioa: int
    target_kw: float
    positive: bool
    data_hex: str


def confirmations(data: bytes) -> list[Confirmation]:
    result = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 6 or data[offset] != 0x68:
            raise ValueError("incomplete c104 raw APDU callback")
        size = data[offset + 1] + 2
        frame = data[offset : offset + size]
        if size < 6 or len(frame) != size:
            raise ValueError("incomplete c104 raw APDU callback")
        offset += size
        if (
            len(frame) != 20
            or frame[2] & 1
            or frame[6] != 50
            or frame[7] != 1
            or frame[8] & 0x3F != 7
            or frame[8] & 0x80
            or frame[19] & 0x80  # select is not the installed direct-execute profile
        ):
            continue
        target = struct.unpack_from("<f", frame, 15)[0]
        if math.isfinite(target):
            result.append(
                Confirmation(
                    int.from_bytes(frame[10:12], "little"),
                    int.from_bytes(frame[12:15], "little"),
                    target,
                    not bool(frame[8] & 0x40),
                    frame.hex(),
                )
            )
    return result


@dataclass
class Pending:
    task_id: str
    ca: int
    target_kw: float
    start_ns: int
    deadline_ns: int
    epoch: int
    responses: list[bool]


class ConfirmationTracker:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.epochs: dict[str, int] = {}
        self.connected: dict[str, bool] = {}
        self.quarantined: set[str] = set()
        self.pending: dict[str, Pending] = {}

    def state(self, station: str, state: str) -> None:
        with self.condition:
            if state.startswith("CLOSED"):
                self.connected[station] = False
            elif state in ("OPEN", "OPEN_MUTED") and not self.connected.get(station, False):
                self.epochs[station] = self.epochs.get(station, 0) + 1
                self.connected[station] = True
                self.quarantined.discard(station)
            self.condition.notify_all()

    def begin(
        self, station: str, task: str, ca: int, target: float, start_ns: int, deadline_ns: int
    ) -> CommandOutcome | None:
        with self.condition:
            if not self.connected.get(station, False):
                return "connection_unavailable"
            if station in self.quarantined:
                return "session_quarantined"
            if station in self.pending:
                raise RuntimeError("concurrent same-station command bypassed serialization")
            self.pending[station] = Pending(
                task, ca, target, start_ns, deadline_ns, self.epochs[station], []
            )
            return None

    def observe(self, station: str, item: Confirmation, now_ns: int) -> str | None:
        with self.condition:
            pending = self.pending.get(station)
            if (
                pending is None
                or not self.connected.get(station)
                or pending.epoch != self.epochs[station]
                or item.common_address != pending.ca
                or item.ioa != 3001
                or abs(item.target_kw - pending.target_kw) > 0.1
                or not pending.start_ns <= now_ns < pending.deadline_ns
            ):
                return None
            pending.responses.append(item.positive)
            self.condition.notify_all()
            return pending.task_id

    def finish(self, station: str) -> tuple[CommandOutcome, bool | None]:
        with self.condition:
            pending = self.pending[station]
            remaining = max(0.0, (pending.deadline_ns - time.monotonic_ns()) / 1e9)
            self.condition.wait_for(
                lambda: (
                    bool(pending.responses)
                    or not self.connected.get(station)
                    or self.epochs[station] != pending.epoch
                ),
                timeout=remaining,
            )
            del self.pending[station]
            if len(pending.responses) == 1:
                positive = pending.responses[0]
                return ("accepted" if positive else "negative_confirmation"), positive
            self.quarantined.add(station)
            if pending.responses:
                return "ambiguous_confirmation", None
            if not self.connected.get(station) or self.epochs[station] != pending.epoch:
                return "connection_lost", None
            # A close after send does not establish delivery/rejection. The
            # sent transaction remains without an effective confirmation.
            return "confirmation_timeout", None
