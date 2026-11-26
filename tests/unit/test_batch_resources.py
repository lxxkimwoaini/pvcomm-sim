"""Synthetic filesystem fixtures validate measurement ownership and accounting."""

from pathlib import Path
from typing import Any

import pytest

from pvcomm.scenario.batch_resources import active_storage, descendants


def test_only_owned_descendants_are_sampled_even_when_threads_spawn(tmp_path: Path) -> None:
    for pid, tid, children in ((10, 10, "20 30"), (20, 21, "40"), (30, 30, "10"), (99, 99, "100")):
        path = tmp_path / str(pid) / "task" / str(tid) / "children"
        path.parent.mkdir(parents=True)
        path.write_text(children)
    assert descendants(10, tmp_path) == [20, 30, 40]


def test_storage_counts_only_active_attempt_and_preserves_sources(tmp_path: Path) -> None:
    directory = tmp_path / "job/attempt_1"
    files = {
        "run/parsed/.parse-a/capture.pdml": 10,
        "run/parsed/.parse-a/tables/x.jsonl": 20,
        "run/.label-index-a/evidence.sqlite": 30,
        "run/raw/pcap/primary.pcap": 40,
    }
    for relative, size in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    state: dict[str, Any] = dict(
        tasks=[
            dict(
                attempts=[
                    dict(status="completed", retained_bytes=1000),
                    dict(status="running", directory="job/attempt_1"),
                ]
            )
        ]
    )
    assert active_storage(tmp_path, state) == dict(
        finalized_retained_bytes=1000,
        active_bytes=100,
        parse_temporary_bytes=30,
        pdml_bytes=10,
        label_index_bytes=30,
    )
    assert all((directory / p).stat().st_size == n for p, n in files.items())
    state["tasks"][0]["attempts"][1]["directory"] = "../escape"
    with pytest.raises(ValueError, match="unsafe"):
        active_storage(tmp_path, state)
