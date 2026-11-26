"""Synthetic bookkeeping fixtures only; these tests do not measure lab throughput."""

import itertools
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pvcomm.config.scenes import canonical_hash
from pvcomm.scenario import concurrency_profile as profile
from pvcomm.scenario.batch import artifact_inventory, duration_s, projected_retained_bytes


def test_fixed_templates_and_storage_reservation() -> None:
    templates = profile.profile_templates()
    assert templates == profile.profile_templates()
    for config, scene, seed in zip(templates, ("N01", "N03"), (920001, 920002), strict=True):
        assert config.maximum_concurrency == 1 and len(config.tasks) == 1
        job = config.tasks[0]
        assert job.kind == "normal" and job.max_attempts == 1
        assert job.scenario.normal_scene == scene
        assert job.scenario.simulation.protocol.seed == seed
        assert len(job.scenario.simulation.protocol.stations) == 30
    retained = 2 * sum(projected_retained_bytes(t, duration_s(t.tasks[0])) for t in templates)
    assert profile.storage_reservation(templates) == (
        retained
        + sum(t.retention.measured_parse_temporary_bytes for t in templates)
        + max(t.minimum_free_bytes for t in templates)
    )


def queue_fixture(root: Path) -> Path:
    config = profile.profile_templates()[0]
    task = config.tasks[0]
    directory = root / "queue" / task.task_id / "attempt_1"
    directory.mkdir(parents=True)
    (directory / "synthetic.txt").write_text("explicit unit fixture, not simulation evidence")
    matrix = root / "matrix.json"
    matrix.write_text(config.model_dump_json())
    (root / "queue/matrix.json").write_text(config.model_dump_json())
    state = dict(
        status="completed",
        code_version="synthetic_sha",
        matrix_sha256=canonical_hash(config.model_dump(mode="json")),
        coverage={},
        tasks=[
            dict(
                task_id=task.task_id,
                kind="normal",
                status="completed",
                attempts=[
                    dict(
                        status="completed",
                        directory=f"{task.task_id}/attempt_1",
                        artifacts=artifact_inventory(directory),
                        result=dict(accepted=True, run_id="run_fixture"),
                    )
                ],
            )
        ],
    )
    (root / "queue/manifest.json").write_text(json.dumps(state))
    (root / "measurements").mkdir()
    (root / "measurements/summary.json").write_text(
        json.dumps(dict(sampling_complete=True, samples=5))
    )
    return matrix


def test_queue_acceptance_requires_retained_checksums(tmp_path: Path) -> None:
    matrix = queue_fixture(tmp_path)
    assert profile.queue_result(tmp_path, matrix, "synthetic_sha")["valid"] is True
    path = next((tmp_path / "queue").rglob("synthetic.txt"))
    path.write_text("altered")
    with pytest.raises(ValueError, match="checksum"):
        profile.queue_result(tmp_path, matrix, "synthetic_sha")


@pytest.mark.parametrize("field", ["source", "matrix", "sampling", "empty_samples", "status"])
def test_incomplete_queue_is_never_a_throughput_success(tmp_path: Path, field: str) -> None:
    matrix = queue_fixture(tmp_path)
    if field in ("source", "matrix"):
        path = tmp_path / "queue/manifest.json"
        state = json.loads(path.read_bytes())
        state["code_version" if field == "source" else "matrix_sha256"] = "changed"
        path.write_text(json.dumps(state))
        with pytest.raises(ValueError, match="fingerprint"):
            profile.queue_result(tmp_path, matrix, "synthetic_sha")
    else:
        if field == "status":
            path = tmp_path / "queue/manifest.json"
            state = json.loads(path.read_bytes())
            state["status"] = "completed_with_failures"
        else:
            path = tmp_path / "measurements/summary.json"
            state = json.loads(path.read_bytes())
            state["sampling_complete" if field == "sampling" else "samples"] = False
        path.write_text(json.dumps(state))
        assert profile.queue_result(tmp_path, matrix, "synthetic_sha")["valid"] is False


@pytest.mark.parametrize("failure", [None, "qc", "sampling", "duplicate_run", "cross_setting"])
def test_finite_profile_stops_on_failure_and_retains_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    monkeypatch.setattr("pvcomm.scenario.concurrency_profile.sys.platform", "linux")
    monkeypatch.setenv("PVCOMM_ALLOW_LAB", "1")
    monkeypatch.setattr(profile, "code_version", lambda: "synthetic_sha")
    monkeypatch.setattr(profile, "lab_namespaces", lambda: [])
    monkeypatch.setattr(
        "pvcomm.scenario.concurrency_profile.shutil.disk_usage",
        lambda _p: SimpleNamespace(free=10**15),
    )
    monkeypatch.setattr(profile, "descendants", lambda _pid: [])
    monkeypatch.setattr(
        "pvcomm.scenario.concurrency_profile.time.sleep", lambda _s: threading.Event().wait(0.001)
    )
    ticks = itertools.count()
    monkeypatch.setattr(
        "pvcomm.scenario.concurrency_profile.time.monotonic", lambda: next(ticks) / 100
    )
    sampled = threading.Event()

    class Sampler:
        def __init__(self, root: Path, *_args: Any, **_kwargs: Any) -> None:
            sampled.clear()

        def sample(self, _pids: list[int]) -> None:
            sampled.set()
            if failure == "sampling":
                raise OSError("synthetic sampling failure")

    calls: list[Path] = []

    def execute(root: Path, _matrix: Path, _source: Path, _sha: str) -> dict[str, Any]:
        assert sampled.wait(5), "test sampler was not invoked"
        calls.append(root)
        return dict(
            exit_code=0,
            valid=failure != "qc",
            run_ids=[
                "duplicate"
                if failure == "duplicate_run"
                else root.name
                if failure == "cross_setting"
                else str(root)
            ],
        )

    monkeypatch.setattr(profile, "ResourceSampler", Sampler)
    monkeypatch.setattr(profile, "execute_profile_queue", execute)
    root = tmp_path / "profile"
    report = profile.profile_concurrency(root)
    assert json.loads((root / "report.json").read_bytes()) == report
    assert report["evaluation_valid"] is (failure is None)
    assert len(calls) == (4 if failure in (None, "cross_setting") else 2)
    assert (report["measured_speedup"] is not None) is (failure is None)
    assert (root / "concurrency_1/summary.json").exists()
    assert (root / "plan.json").exists()
    with pytest.raises(FileExistsError):
        profile.profile_concurrency(root)


def test_active_lab_or_low_space_never_starts_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pvcomm.scenario.concurrency_profile.sys.platform", "linux")
    monkeypatch.setenv("PVCOMM_ALLOW_LAB", "1")
    monkeypatch.setattr(profile, "code_version", lambda: "synthetic_sha")
    monkeypatch.setattr(profile, "lab_namespaces", lambda: ["pvcl-owned_by_another_run"])
    root = tmp_path / "profile"
    with pytest.raises(RuntimeError, match="active"):
        profile.profile_concurrency(root)
    assert not root.exists()
    monkeypatch.setattr(profile, "lab_namespaces", lambda: [])
    monkeypatch.setattr(
        "pvcomm.scenario.concurrency_profile.shutil.disk_usage", lambda _p: SimpleNamespace(free=1)
    )
    with pytest.raises(RuntimeError, match="storage"):
        profile.profile_concurrency(root)
    assert not root.exists()


def test_storage_sums_simultaneous_samples_not_separate_maxima(tmp_path: Path) -> None:
    paths = []
    for index, retained in enumerate((100, 200)):
        root = tmp_path / str(index)
        (root / "queue").mkdir(parents=True)
        (root / "queue/summary.json").write_text(
            json.dumps(
                dict(tasks=[dict(attempts=[dict(status="completed", retained_bytes=retained)])])
            )
        )
        paths.append((root, root / "matrix.json"))
    totals = profile.sample_storage(paths)
    assert totals["finalized_retained_bytes"] == 300
    assert totals["parse_temporary_bytes"] == 0
