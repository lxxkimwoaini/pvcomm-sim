"""Explicit synthetic IPC fixtures; never simulation evidence."""

from pathlib import Path

import pytest

from pvcomm.storage.jsonl import MAX_SNAPSHOT_BYTES, last_complete_record


def test_tail_is_bounded_independent_of_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ipc.jsonl"
    with path.open("wb") as stream:
        for _ in range(200_000):
            stream.write(b'{"seq":1}\n')
        stream.write(b'{"seq":2}\n{"partial":')
    # Whole-file reads are forbidden; this also guards against reintroducing read_bytes.
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("unbounded IPC read"))
    assert last_complete_record(path) == {"seq": 2}


def test_tail_rejects_oversized_and_nonobject_rows(tmp_path: Path) -> None:
    path = tmp_path / "ipc.jsonl"
    path.write_bytes(b"x" * (MAX_SNAPSHOT_BYTES + 1))
    with pytest.raises(ValueError, match="maximum"):
        last_complete_record(path)
    path.write_bytes(b"{}\n" + b"x" * (MAX_SNAPSHOT_BYTES + 1) + b"\n")
    with pytest.raises(ValueError):
        last_complete_record(path)
    path.write_bytes(b"[]\n")
    with pytest.raises(ValueError, match="object"):
        last_complete_record(path)
