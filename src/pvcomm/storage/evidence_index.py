"""Temporary disk index for Oracle evidence; never a source of online observations."""

import json
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, overload


class EvidenceIndex:
    """One serialized copy per record with an explicit, idempotent lifecycle."""

    def __init__(self, root: Path) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix=".label-index-", dir=root)
        self.connection = sqlite3.connect(Path(self.temporary.name) / "evidence.sqlite")
        self.closed = False
        self.connection.execute("PRAGMA cache_size=-8192")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE records (ordinal INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, "
            "source_path TEXT NOT NULL, group_id INTEGER NOT NULL, station_id TEXT, "
            "payload TEXT NOT NULL)"
        )

    def add(
        self, identity: str, path: str, group: int, station: str | None, row: dict[str, Any]
    ) -> None:
        try:
            self.connection.execute(
                "INSERT INTO records(identity,source_path,group_id,station_id,payload) "
                "VALUES (?,?,?,?,?)",
                (identity, path, group, station, json.dumps(row, allow_nan=False)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate source record identity") from exc

    def freeze(self) -> None:
        self.connection.execute("CREATE INDEX station_rows ON records(group_id,station_id,ordinal)")
        self.connection.commit()
        self.connection.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        if not self.closed:
            self.connection.close()
            self.temporary.cleanup()
            self.closed = True


class EvidenceRecords(Mapping[str, tuple[str, dict[str, Any]]]):
    def __init__(self, index: EvidenceIndex) -> None:
        self.index = index

    def __getitem__(self, key: str) -> tuple[str, dict[str, Any]]:
        record = self.index.connection.execute(
            "SELECT source_path,payload FROM records WHERE identity=?", (key,)
        ).fetchone()
        if record is None:
            raise KeyError(key)
        return record[0], json.loads(record[1])

    def __iter__(self) -> Iterator[str]:
        cursor = self.index.connection.execute("SELECT identity FROM records ORDER BY ordinal")
        try:
            for row in cursor:
                yield row[0]
        finally:
            cursor.close()

    def __len__(self) -> int:
        return int(self.index.connection.execute("SELECT count(*) FROM records").fetchone()[0])


class EvidenceRows(Sequence[dict[str, Any]]):
    def __init__(self, index: EvidenceIndex, group: int, station: str | None = None) -> None:
        self.database, self.group, self.station = index, group, station

    def for_station(self, station: str) -> "EvidenceRows":
        return EvidenceRows(self.database, self.group, station)

    def selection(self) -> tuple[str, tuple[object, ...]]:
        if self.station is None:
            return "group_id=?", (self.group,)
        # Lifecycle/coverage records have no station and apply to every view.
        return "group_id=? AND (station_id=? OR station_id IS NULL)", (self.group, self.station)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        where, parameters = self.selection()
        cursor = self.database.connection.execute(
            f"SELECT payload FROM records WHERE {where} ORDER BY ordinal", parameters
        )
        try:
            for row in cursor:
                yield json.loads(row[0])
        finally:
            cursor.close()

    def __len__(self) -> int:
        where, parameters = self.selection()
        return int(
            self.database.connection.execute(
                f"SELECT count(*) FROM records WHERE {where}", parameters
            ).fetchone()[0]
        )

    @overload
    def __getitem__(self, key: int) -> dict[str, Any]: ...

    @overload
    def __getitem__(self, key: slice) -> list[dict[str, Any]]: ...

    def __getitem__(self, key: int | slice) -> dict[str, Any] | list[dict[str, Any]]:
        if isinstance(key, slice):
            return [self[i] for i in range(*key.indices(len(self)))]
        position = key if key >= 0 else len(self) + key
        if position < 0:
            raise IndexError(key)
        where, parameters = self.selection()
        record = self.database.connection.execute(
            f"SELECT payload FROM records WHERE {where} ORDER BY ordinal LIMIT 1 OFFSET ?",
            (*parameters, position),
        ).fetchone()
        if record is None:
            raise IndexError(key)
        result: dict[str, Any] = json.loads(record[0])
        return result
