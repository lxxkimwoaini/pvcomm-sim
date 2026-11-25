"""Rebuildable on-disk M2 index. Only appended observed bytes are ingested."""

import json
import sqlite3
from pathlib import Path
from typing import Any


class ObservationIndex:
    def __init__(self, database: Path) -> None:
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE observations (stamp INTEGER, id TEXT PRIMARY KEY, "
            "station TEXT, kind TEXT, ioa INTEGER, payload TEXT)"
        )
        self.connection.execute("CREATE INDEX time_idx ON observations(stamp, id)")
        self.connection.execute(
            "CREATE INDEX series_idx ON observations(station, kind, ioa, stamp, id)"
        )
        self.connection.execute("CREATE INDEX state_idx ON observations(station, kind, stamp, id)")
        self.connection.execute("CREATE INDEX kind_idx ON observations(kind, stamp, id)")
        self.offsets: dict[Path, int] = {}
        self.ingested_bytes = 0
        self.raw_totals: dict[tuple[Path, str], tuple[int, int]] = {}

    def ingest(self, path: Path, *, protocol: bool = False, raw_callbacks: bool = False) -> None:
        offset = self.offsets.get(path, 0)
        if path.stat().st_size < offset:
            raise ValueError("observed append-only evidence was truncated")
        fields = (
            {
                "apdu_id",
                "available_at",
                "frame_type",
                "type_id",
                "cot",
                "ca",
                "send_seq",
                "recv_seq",
            }
            if protocol
            else {
                "event_id",
                "event_type",
                "available_at",
                "sim_elapsed_ms",
                "station_id",
                "ioa",
                "value",
                "state",
                "action",
                "target_kw",
                "positive_confirmation",
                "task_id",
                "dispatch_id",
                "region_ids",
                "requested_kw",
                "allocated_kw",
                "unallocated_kw",
                "targets_kw",
            }
        )
        totals = dict(self.raw_totals)
        with path.open("rb") as stream, self.connection:
            stream.seek(offset)
            while True:
                line = stream.readline(1_048_577)
                if len(line) > 1_048_576:
                    raise ValueError("observed row exceeds maximum size")
                if not line or not line.endswith(b"\n"):
                    break
                raw = json.loads(line)
                if raw_callbacks:
                    direction = raw.get("direction")
                    if (
                        direction not in ("master_send", "master_receive")
                        or "available_at" not in raw
                    ):
                        raise ValueError("invalid master raw callback")
                    count, size = totals.get((path, direction), (0, 0))
                    count, size = count + 1, size + raw["length"]
                    totals[(path, direction)] = count, size
                    row = {
                        "event_id": raw["event_id"],
                        "available_at": raw["available_at"],
                        "event_type": "statistics_" + direction,
                        "count": count,
                        "bytes": size,
                    }
                elif "available_at" not in raw or (
                    not protocol and not raw.get("event_type", "").startswith("master_")
                ):
                    raise ValueError("invalid master observation contract")
                else:
                    row = {k: v for k, v in raw.items() if k in fields}
                identity = row["apdu_id" if protocol else "event_id"]
                try:
                    self.connection.execute(
                        "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            row["available_at"],
                            identity,
                            row.get("station_id"),
                            "protocol" if protocol else row["event_type"],
                            row.get("ioa"),
                            json.dumps(row),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("duplicate observation identity") from exc
                offset = stream.tell()
        self.ingested_bytes += offset - self.offsets.get(path, 0)
        self.offsets[path] = offset
        self.raw_totals = totals

    def bounds(self) -> tuple[int, int]:
        low = self.connection.execute(
            "SELECT stamp FROM observations WHERE kind != 'protocol' ORDER BY stamp, id LIMIT 1"
        ).fetchone()
        high = self.connection.execute(
            "SELECT stamp FROM observations WHERE kind != 'protocol' "
            "ORDER BY stamp DESC, id DESC LIMIT 1"
        ).fetchone()
        return (low[0] if low else 0, high[0] if high else 0)

    def rows(
        self,
        cursor: int,
        *,
        station: str | None = None,
        kind: str | None = None,
        ioa: int | None = None,
        limit: int = 100,
        before: tuple[int, str] | None = None,
    ) -> list[dict[str, Any]]:
        where = ["stamp <= ?"]
        args: list[str | int] = [cursor]
        for column, value in (("station", station), ("kind", kind), ("ioa", ioa)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if kind is None:
            where.append(
                "kind NOT IN ('protocol', 'statistics_master_send', 'statistics_master_receive')"
            )
        if before is not None:
            where.append("(stamp, id) < (?, ?)")
            args.extend(before)
        args.append(limit)
        records = self.connection.execute(
            f"SELECT payload FROM observations WHERE {' AND '.join(where)} "
            "ORDER BY stamp DESC, id DESC LIMIT ?",
            args,
        ).fetchall()
        return [json.loads(row[0]) for row in reversed(records)]
