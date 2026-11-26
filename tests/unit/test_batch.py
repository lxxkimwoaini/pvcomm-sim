"""Synthetic executor fixtures exercise queue bookkeeping, never simulation acceptance."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.batch import BatchConfig, independent_pair_templates
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.scenario.batch import coverage, pair_counts, run_batch


def matrix(*, attempts: int = 2) -> BatchConfig:
    return BatchConfig.model_validate(
        dict(
            matrix_id="unit_queue",
            tasks=[
                dict(
                    kind="normal",
                    task_id="task_1",
                    max_attempts=attempts,
                    scenario=normal_scenario("N01").model_dump(mode="json"),
                )
            ],
        )
    )


@pytest.fixture
def authorized_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVCOMM_ALLOW_LAB", "1")
    monkeypatch.setattr("pvcomm.scenario.batch.code_version", lambda: "unit-only-code")


def test_finite_retry_retains_failed_artifacts_and_resume_skips_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    calls = []

    def executor(task: Any, path: Path, config: Any) -> dict[str, Any]:
        calls.append(path)
        (path / "unit-only.txt").write_text("synthetic executor fixture")
        if len(calls) == 1:
            raise RuntimeError("synthetic first-attempt failure")
        return dict(accepted=True, run_id="unit_run", fault_type=None)

    monkeypatch.setattr("pvcomm.scenario.batch.execute_job", executor)
    root = tmp_path / "batch"
    state = run_batch(matrix(), root)
    assert state["status"] == "completed" and len(calls) == 2
    first, second = state["tasks"][0]["attempts"]
    assert first["status"] == "failed" and second["status"] == "completed"
    assert (calls[0] / "failure.txt").is_file() and (calls[0] / "unit-only.txt").is_file()
    assert first["artifacts"] and all(len(a["sha256"]) == 64 for a in first["artifacts"])
    resumed = run_batch(matrix(), root, resume=True)
    assert len(calls) == 2 and resumed["coverage"]["valid_normal_runs"] == 1
    with pytest.raises(FileExistsError):
        run_batch(matrix(), root)


def test_cancel_then_resume_creates_new_attempt_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    calls = []

    def executor(task: Any, path: Path, config: Any) -> dict[str, Any]:
        calls.append(path)
        (path / "retained.txt").write_text("unit cancellation evidence")
        if len(calls) == 1:
            raise KeyboardInterrupt()
        return dict(accepted=True, run_id="unit_after_cancel", fault_type="A08")

    monkeypatch.setattr("pvcomm.scenario.batch.execute_job", executor)
    root = tmp_path / "cancel"
    with pytest.raises(KeyboardInterrupt):
        run_batch(matrix(), root)
    assert json.loads((root / "manifest.json").read_bytes())["status"] == "cancelled"
    state = run_batch(matrix(), root, resume=True)
    assert state["status"] == "completed" and "error" not in state
    assert (
        calls[0] != calls[1]
        and (calls[0] / "retained.txt").read_text() == "unit cancellation evidence"
    )
    assert state["coverage"]["valid_fault_runs"] == {"A08": 1}


def test_crashed_attempt_consumes_retry_budget_and_completed_jobs_are_not_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    monkeypatch.setattr("pvcomm.scenario.batch.execute_job", lambda *_: dict(accepted=False))
    root = tmp_path / "crashed"
    state = run_batch(matrix(attempts=1), root)
    state["tasks"][0].update(status="running")
    state["tasks"][0]["attempts"][0]["status"] = "running"
    (root / "manifest.json").write_text(json.dumps(state))
    resumed = run_batch(matrix(attempts=1), root, resume=True)
    assert resumed["status"] == "completed_with_failures"
    assert resumed["tasks"][0]["attempts"][0]["status"] == "interrupted"
    assert resumed["coverage"]["attempts"] == 1


def test_resume_rejects_matrix_code_changes_and_concurrent_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    monkeypatch.setattr("pvcomm.scenario.batch.execute_job", lambda *_: dict(accepted=False))
    root = tmp_path / "locked"
    run_batch(matrix(attempts=1), root)
    with pytest.raises(ValueError, match="same frozen"):
        run_batch(matrix(attempts=2), root, resume=True)
    with closing(sqlite3.connect(root / ".execution-lock.sqlite")) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            run_batch(matrix(attempts=1), root, resume=True)
    monkeypatch.setattr("pvcomm.scenario.batch.code_version", lambda: "other-code")
    with pytest.raises(ValueError, match="same frozen"):
        run_batch(matrix(attempts=1), root, resume=True)


def test_capacity_gate_stops_before_executor_and_retains_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    monkeypatch.setattr(
        "pvcomm.scenario.batch.shutil.disk_usage", lambda _: type("Disk", (), dict(free=1))()
    )
    monkeypatch.setattr(
        "pvcomm.scenario.batch.execute_job", lambda *_: pytest.fail("must not start")
    )
    root = tmp_path / "budget"
    with pytest.raises(RuntimeError, match="storage budget blocked"):
        run_batch(matrix(), root)
    state = json.loads((root / "manifest.json").read_bytes())
    assert state["status"] == "blocked" and state["coverage"]["attempts"] == 0


def test_full_matrix_storage_projection_prevents_launch(
    tmp_path: Path, authorized_fixture: None
) -> None:
    raw = matrix().model_dump(mode="json")
    raw["maximum_output_bytes"] = 1024**3
    raw["tasks"] = [{**raw["tasks"][0], "task_id": f"job_{i}"} for i in range(20)]
    config = BatchConfig.model_validate(raw)
    with pytest.raises(ValueError, match="projected retained storage"):
        run_batch(config, tmp_path / "too_large")
    assert not (tmp_path / "too_large").exists()


def test_nonmatching_valid_pair_is_not_an_invalid_experiment() -> None:
    record = dict(data_quality_passed=True, business_contrast=True, hard_pair_selected=False)
    counts = pair_counts(record)
    assert counts == dict(
        attempted_pairs=1, valid_pairs=1, business_contrast_pairs=1, matched_pairs=0
    )
    state = dict(
        tasks=[
            dict(
                kind="pair",
                pair_type="P04",
                status="completed",
                attempts=[
                    dict(
                        status="completed",
                        result=dict(
                            accepted=True,
                            counts=counts,
                            independent_runs=[
                                dict(run_id="unit_normal", fault_type=None),
                                dict(run_id="unit_changed", fault_type="A06"),
                            ],
                        ),
                    )
                ],
            )
        ]
    )
    result = coverage(state)
    assert result["pairs"]["P04"] == counts
    assert result["valid_fault_runs"] == {"A06": 1} and result["valid_normal_runs"] == 1
    state["tasks"].append(state["tasks"][0])
    with pytest.raises(ValueError, match="duplicate independent"):
        coverage(state)


def test_preregistered_pair_repeats_have_shared_conditions_and_independent_backgrounds() -> None:
    cases = independent_pair_templates()
    assert len(cases) == 12 and cases == independent_pair_templates()
    assert len({p.normal.simulation.protocol.seed for p in cases}) == 12
    assert not {p.normal.simulation.weather.source_start.day for p in cases} & {10, 11}
    for pair in cases:
        assert pair.shared_conditions(pair.normal) == pair.shared_conditions(
            pair.changed.background
        )


def test_batch_contract_rejects_missing_thresholds_duplicates_and_concurrency() -> None:
    raw = matrix().model_dump(mode="json")
    raw["tasks"].append(raw["tasks"][0])
    with pytest.raises(ValueError, match="unique"):
        BatchConfig.model_validate(raw)
    raw = matrix().model_dump(mode="json")
    raw["maximum_concurrency"] = 2
    with pytest.raises(ValueError):
        BatchConfig.model_validate(raw)
    raw["maximum_concurrency"] = 1
    raw["tasks"] = [
        dict(
            kind="pair",
            task_id="pair_1",
            pair=independent_pair_templates()[0].model_dump(mode="json"),
        )
    ]
    with pytest.raises(ValueError, match="thresholds frozen"):
        BatchConfig.model_validate(raw)


def test_resume_checks_evidence_hashes_and_rejects_state_path_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized_fixture: None
) -> None:
    def executor(task: Any, path: Path, config: Any) -> dict[str, Any]:
        (path / "evidence.txt").write_text("synthetic retained evidence")
        return dict(accepted=True, run_id="unit_integrity", fault_type=None)

    monkeypatch.setattr("pvcomm.scenario.batch.execute_job", executor)
    root = tmp_path / "integrity"
    state = run_batch(matrix(), root)
    evidence_path = root / "task_1/attempt_1/evidence.txt"
    evidence_path.write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        run_batch(matrix(), root, resume=True)
    evidence_path.write_text("synthetic retained evidence")
    state["tasks"][0]["attempts"][0]["directory"] = "../escape"
    (root / "manifest.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="unsafe retained"):
        run_batch(matrix(), root, resume=True)
