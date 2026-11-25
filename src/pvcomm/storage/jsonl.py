"""Bounded reads of single-writer append-only IPC (not an evidence exporter)."""

import json
from pathlib import Path
from typing import Any

MAX_SNAPSHOT_BYTES = 65_536


def last_complete_record(path: Path) -> dict[str, Any] | None:
    """Read at most two maximum records; a partial final row is never consumed.

    Writers must keep each encoded row within MAX_SNAPSHOT_BYTES. A completed
    malformed row is an error, including at EOF; it is never silently skipped.
    """
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - 2 * MAX_SNAPSHOT_BYTES)
            stream.seek(start)
            tail = stream.read(2 * MAX_SNAPSHOT_BYTES)
    except FileNotFoundError:
        return None
    end = tail.rfind(b"\n")
    if end < 0:
        if size > MAX_SNAPSHOT_BYTES:
            raise ValueError("IPC record exceeds maximum size")
        return None
    begin = tail.rfind(b"\n", 0, end) + 1
    if (start and begin == 0) or end - begin > MAX_SNAPSHOT_BYTES:
        raise ValueError("IPC record exceeds maximum size")
    if len(tail) - end - 1 > MAX_SNAPSHOT_BYTES:
        raise ValueError("IPC partial record exceeds maximum size")
    value = json.loads(tail[begin:end])
    if not isinstance(value, dict):
        raise ValueError("IPC snapshot must be an object")
    return value
