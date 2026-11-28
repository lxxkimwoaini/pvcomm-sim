"""Synthetic /proc fixtures only; no simulation or privileged resources."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def recovery() -> Any:
    spec = importlib.util.spec_from_file_location("recovery", "scripts/resume_server_parallel.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process(proc: Path, pid: int) -> None:
    root = proc / str(pid)
    root.mkdir()
    stat = ["0"] * 22
    stat[11], stat[12], stat[21] = "200", "100", "10"
    (root / "stat").write_text(f"{pid} (fixture worker) " + " ".join(stat))
    (root / "io").write_text("read_bytes: 40\nwrite_bytes: 80\n")


def test_denied_io_keeps_cpu_and_memory_and_marks_io_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = recovery()
    process(tmp_path, 11)
    process(tmp_path, 12)
    original = Path.read_text

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == tmp_path / "12/io":
            raise PermissionError("fixture denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    row = module.process_sample([11, 12, 12], proc=tmp_path, clock_ticks=100, page_size=4096)
    assert row["cpu_s"] == 6
    assert row["rss_bytes"] == 81920
    assert row["owned_pids"] == [11, 12]
    assert row["disk_write_bytes"] is None
    assert row["readable_process_disk_write_bytes"] == 80
    assert row["process_stat_complete"] and not row["process_io_complete"]
    assert row["sample_errors"] == [dict(pid=12, field="io", error="PermissionError")]


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError, PermissionError])
def test_disappearing_or_denied_process_does_not_stop_sampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    module = recovery()
    process(tmp_path, 11)
    original = Path.read_text

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == tmp_path / "12/stat":
            raise error("fixture race")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    row = module.process_sample([11, 12], proc=tmp_path, clock_ticks=100, page_size=4096)
    assert row["cpu_s"] == 3 and row["rss_bytes"] == 40960
    assert row["owned_pids"] == [11]
    assert not row["process_stat_complete"]
    assert row["disk_read_bytes"] is None
    assert row["sample_errors"] == [dict(pid=12, field="stat", error=error.__name__)]


def test_complete_sample_reports_actual_io(tmp_path: Path) -> None:
    process(tmp_path, 11)
    row = recovery().process_sample([11], proc=tmp_path, clock_ticks=100, page_size=4096)
    assert row["disk_read_bytes"] == 40 and row["disk_write_bytes"] == 80
    assert row["process_io_complete"] and row["sample_errors"] == []


def test_recovery_samples_identity_checked_reparented_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pvcomm.clock import ClockOrigin
    from pvcomm.scenario import batch_resources

    module = recovery()
    resources: Any = importlib.import_module("pvcomm.scenario.resources")
    (tmp_path / "parallel-index.json").write_text(
        json.dumps(
            dict(
                workers=dict(
                    active=dict(status="running", pid=10),
                    reused=dict(status="running", pid=20),
                    completed=dict(status="completed", pid=30),
                )
            )
        )
    )
    expected = [99, 10, 11]

    def sample(pids: list[int], **_kwargs: Any) -> dict[str, Any]:
        assert pids == expected
        return dict(cpu_s=1, owned_pids=sorted(pids), sample_errors=[])

    monkeypatch.setattr(module, "process_sample", sample)
    monkeypatch.setattr(module, "os", SimpleNamespace(getpid=lambda: 99, sysconf=lambda _: 100))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(batch_resources, "descendants", lambda pid: [pid + 1])
    original = resources.ResourceSampler
    try:
        module.install_sampler(tmp_path, {"identity_alive": lambda record: record["pid"] == 10})
        sampler = resources.ResourceSampler(tmp_path, ClockOrigin.capture())
        sampler.sample([])
        row = json.loads((tmp_path / "resources.jsonl").read_bytes())
        assert row["owned_pids"] == [10, 11, 99]
        assert row["cpu_percent_one_core"] is None
    finally:
        resources.ResourceSampler = original
