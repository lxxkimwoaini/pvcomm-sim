"""Disposable row offsets for immutable Oracle evidence; sources remain untouched."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Any

ROW_BUDGET = 4 * 1024**2


def precise(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: str(v)
            if isinstance(v, int) and (k.endswith("_ns") or k == "available_at")
            else precise(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [precise(v) for v in value]
    return value


class RowOffsets:
    def __init__(self, path: Path, database: Path, run_id: str) -> None:
        self.path, self.run_id = path, run_id
        self.signature = self.stat()
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.database = database
        self.total = 0
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.execute(
            "CREATE TABLE offsets (ordinal INTEGER PRIMARY KEY, identity TEXT UNIQUE, "
            "position INTEGER, length INTEGER)"
        )
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream, self.connection:
                while True:
                    position = stream.tell()
                    line = stream.readline(ROW_BUDGET + 1)
                    if not line:
                        break
                    if len(line) > ROW_BUDGET or not line.endswith(b"\n"):
                        raise ValueError("incomplete or oversized diagnostic record")
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get("run_id", run_id) != run_id:
                        raise ValueError("cross-run diagnostic record")
                    identity = row.get("event_id")
                    if "apdu_id" in row:
                        identity = row["apdu_id"]
                        if "object_index" in row:
                            identity += f":{row['object_index']}"
                    self.connection.execute(
                        "INSERT INTO offsets VALUES (?, ?, ?, ?)",
                        (self.total, identity, position, len(line)),
                    )
                    self.total += 1
                    digest.update(line)
            self.sha256 = digest.hexdigest()
            self.check()
        except sqlite3.IntegrityError as exc:
            self.close()
            raise ValueError("duplicate diagnostic record identity") from exc
        except BaseException:
            self.close()
            raise

    def stat(self) -> tuple[int, int]:
        info = self.path.stat()
        return info.st_size, info.st_mtime_ns

    def check(self) -> None:
        if self.stat() != self.signature:
            raise ValueError("immutable diagnostic source changed")

    def read(self, offsets: list[tuple[int, int]]) -> list[dict[str, Any]]:
        self.check()
        with self.path.open("rb") as stream:
            result = []
            for position, length in offsets:
                stream.seek(position)
                result.append(json.loads(stream.read(length)))
        self.check()
        return result

    def page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT position,length FROM offsets WHERE ordinal>=? ORDER BY ordinal LIMIT ?",
            (offset, limit),
        ).fetchall()
        return self.read(rows)

    def record(self, identity: str) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT position,length FROM offsets WHERE identity=?", (identity,)
        ).fetchall()
        if not rows:
            raise FileNotFoundError("unknown evidence record")
        return self.read(rows)[0]

    def close(self) -> None:
        self.connection.close()
        self.database.unlink(missing_ok=True)


class DiagnosticIndexes:
    """At most four rebuildable SQLite indexes and bounded per-file page reads."""

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pvcomm-oracle-index-")
        self.indexes: dict[Path, RowOffsets] = {}
        self.lock = threading.RLock()
        self.finalizer = weakref.finalize(self, self.cleanup, self.indexes, self.temporary)

    @staticmethod
    def cleanup(
        indexes: dict[Path, RowOffsets], temporary: tempfile.TemporaryDirectory[str]
    ) -> None:
        for index in indexes.values():
            index.close()
        temporary.cleanup()

    def get(self, path: Path, run_id: str) -> RowOffsets:
        # Caller holds lock across lookup and reads; FastAPI uses worker threads.
        if path in self.indexes:
            index = self.indexes[path]
            index.check()
            if index.run_id != run_id:
                raise ValueError("cross-run diagnostic index")
            del self.indexes[path]
        else:
            if len(self.indexes) == 4:
                self.indexes.pop(next(iter(self.indexes))).close()
            database = Path(self.temporary.name) / (hashlib.sha256(str(path).encode()).hexdigest())
            index = RowOffsets(path, database, run_id)
        self.indexes[path] = index
        return index
