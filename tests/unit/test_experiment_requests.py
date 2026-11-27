"""Synthetic request bookkeeping tests; no mock output is research evidence."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pvcomm.config.batch import BatchConfig
from pvcomm.config.scenes import canonical_hash
from pvcomm.config.workbench import (
    ExperimentRequest,
    requested_matrix,
    validate_development_scope,
)
from pvcomm.scenario.batch import save_json, summary
from pvcomm.scenario.requests import execute_requests, submit_request
from pvcomm.web.app import create_app
from pvcomm.web.experiments import ExperimentRepository


def body(**changes: Any) -> dict[str, Any]:
    return dict(template="N09", seed=90123) | changes


@pytest.mark.parametrize(
    "changes",
    [
        dict(template="A03", strength=1),
        dict(template="A05", strength=3),
        dict(template="A04", strength=2, station_ids=["PV99"]),
        dict(template="A04", strength=2, station_ids=["PV01", "PV01"]),
        dict(template="P04", site_count=30),
        dict(template="N09", scope="region"),
        dict(template="A08", strength=1, region_id="R3"),
        dict(command="rm -rf /"),
        dict(seed=True),
        dict(output="/etc"),
    ],
)
def test_reject_unsupported_parameters(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExperimentRequest.model_validate(body(**changes))


@pytest.mark.parametrize(
    "code,strength", [("A01", 20), ("A02", 200), ("A04", 2), ("A05", 5), ("A06", 30), ("A08", 1)]
)
def test_multisite_requests_reuse_validated_templates(code: str, strength: int) -> None:
    request = ExperimentRequest.model_validate(
        body(template=code, strength=strength, site_count=10, scope="region")
    )
    matrix = requested_matrix(request, "experiment_test")
    task = matrix.tasks[0]
    assert task.kind == "fault"
    assert len(task.scenario.background.simulation.protocol.stations) == 10
    assert task.scenario.background.simulation.protocol.seed == request.seed
    assert len(task.scenario.target_stations()) > 1


def test_api_opt_in_and_submission_cannot_execute_or_leak_to_m2(tmp_path: Path) -> None:
    disabled = TestClient(create_app(tmp_path / "runs"))
    assert (
        disabled.get("/api/diagnostics/experiments/catalog").json()["submission_enabled"] is False
    )
    assert disabled.post("/api/diagnostics/experiments", json=body()).status_code == 503
    root = tmp_path / "experiments"
    client = TestClient(create_app(tmp_path / "runs", root))
    response = client.post("/api/diagnostics/experiments", json=body())
    assert response.status_code == 201
    record = response.json()
    identity = record["experiment_id"]
    assert record["status"] == "queued" and record["queue"] is None
    assert not (root / identity / "queue").exists()
    matrix = BatchConfig.model_validate_json((root / identity / "matrix.json").read_bytes())
    assert matrix.tasks[0].kind == "normal"
    assert matrix.tasks[0].scenario.simulation.protocol.seed == 90123
    assert client.get("/api/runs").json() == {"runs": []}
    assert client.get("/api/runs/" + identity + "/snapshot").status_code == 404
    assert client.get("/api/diagnostics/experiments").json()["total"] == 1
    assert client.get("/api/diagnostics/experiments?limit=100").status_code == 422
    assert (
        client.post(
            "/api/diagnostics/experiments",
            json=body(),
            headers={"Origin": "https://untrusted.invalid"},
        ).status_code
        == 403
    )
    assert client.post("/api/diagnostics/experiments", json=body(output="/tmp")).status_code == 422
    assert client.post("/api/diagnostics/experiments", json=body(template="P04")).status_code == 422


def test_small_development_scope_rejects_large_submission_but_preserves_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "experiments"
    client = TestClient(create_app(tmp_path / "runs", root))
    assert client.get("/api/diagnostics/experiments/catalog").json()["site_counts"] == [3, 10]
    assert client.post("/api/diagnostics/experiments", json=body(site_count=30)).status_code == 422
    assert not root.exists()  # Rejection happens before any experiment is queued.
    historical = ExperimentRequest.model_validate(body(site_count=30))
    matrix = requested_matrix(historical, "historical_30")
    assert matrix.tasks[0].kind == "normal"
    assert len(matrix.tasks[0].scenario.simulation.protocol.stations) == 30
    with pytest.raises(ValueError, match="3 or 10"):
        validate_development_scope(historical, matrix)
    ten = requested_matrix(ExperimentRequest.model_validate(body(site_count=10)), "ten")
    with pytest.raises(ValueError, match="counts disagree"):
        validate_development_scope(ExperimentRequest.model_validate(body()), ten)


def test_worker_defers_historical_large_request_and_continues_small_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PVCOMM_ALLOW_LAB", "1")
    monkeypatch.setattr("pvcomm.scenario.requests.sys.platform", "linux")
    old = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    directory = tmp_path / old["experiment_id"]
    historical = ExperimentRequest.model_validate(body(site_count=30))
    matrix = requested_matrix(historical, directory.name)
    old.update(
        request=historical.model_dump(mode="json"),
        matrix_sha256=canonical_hash(matrix.model_dump(mode="json")),
    )
    save_json(directory / "request.json", old)
    save_json(directory / "matrix.json", matrix.model_dump(mode="json"))
    # Synthetic interrupted queue projection, not simulation or acceptance evidence.
    (directory / "queue").mkdir()
    save_json(directory / "queue/summary.json", dict(status="running"))
    original = (directory / "matrix.json").read_bytes()
    small = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    calls = []

    def synthetic(config: BatchConfig, output: Path, **_kwargs: Any) -> dict[str, str]:
        calls.append(config.matrix_id)
        return dict(status="completed", updated_at="synthetic-unit-fixture")

    monkeypatch.setattr("pvcomm.scenario.requests.run_batch", synthetic)
    assert execute_requests(tmp_path, resume=True) == [small["experiment_id"]]
    assert calls == [small["experiment_id"]]
    assert execute_requests(tmp_path, resume=True) == []
    assert (directory / "matrix.json").read_bytes() == original
    view = ExperimentRepository(tmp_path).view(directory.name)
    assert view.status == "deferred_scope" and view.request["site_count"] == 30
    assert view.queue == {"status": "running"}  # Original projection is retained.


def test_poll_reads_compact_projection_and_rejects_symlink(tmp_path: Path) -> None:
    request = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    directory = tmp_path / request["experiment_id"]
    queue = directory / "queue"
    queue.mkdir()
    state = dict(
        status="completed",
        tasks=[
            dict(
                attempts=[dict(status="completed", artifacts=[dict(path="large", sha256="x" * 64)])]
            )
        ],
    )
    save_json(queue / "summary.json", summary(state))
    (queue / "manifest.json").write_text("invalid full manifest must never be read by polling")
    view = ExperimentRepository(tmp_path).view(directory.name)
    assert view.status == "completed" and "artifacts" not in json.dumps(view.model_dump())
    # Metadata path traversal is rejected even for an existing request directory.
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text("{}")
    target = directory / "worker.json"
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable on this Windows account")
    with pytest.raises(ValueError, match="escapes"):
        ExperimentRepository(tmp_path).view(directory.name)


def test_worker_checks_hash_lock_and_never_reexecutes_completed_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PVCOMM_ALLOW_LAB", "1")
    monkeypatch.setattr("pvcomm.scenario.requests.sys.platform", "linux")
    record = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    calls = []

    def synthetic(matrix: BatchConfig, output: Path, **_kwargs: Any) -> dict[str, str]:
        calls.append(matrix.matrix_id)
        return dict(status="completed", updated_at="synthetic-unit-fixture")

    monkeypatch.setattr("pvcomm.scenario.requests.run_batch", synthetic)
    with sqlite3.connect(tmp_path / ".worker-lock.sqlite", timeout=0) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            execute_requests(tmp_path)
    assert execute_requests(tmp_path) == [record["experiment_id"]]
    assert execute_requests(tmp_path) == [] and len(calls) == 1
    other = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    path = tmp_path / other["experiment_id"] / "matrix.json"
    matrix = json.loads(path.read_bytes())
    matrix["tasks"][0]["scenario"]["simulation"]["protocol"]["seed"] += 1
    save_json(path, matrix)
    with pytest.raises(ValueError, match="checksum"):
        execute_requests(tmp_path)
    assert len(calls) == 1


def test_label_diagnostics_preserve_ns_and_evidence_reference(tmp_path: Path) -> None:
    request = submit_request(tmp_path, ExperimentRequest.model_validate(body()))
    identity = request["experiment_id"]
    run = tmp_path / identity / "queue/task_1/attempt_1/run_fixture"
    labels = run / "labels/v04-label-v3/event_labels.jsonl"
    labels.parent.mkdir(parents=True)
    value = dict(
        station_id="PV01",
        fault_applied=True,
        service_violation=False,
        findings=[
            dict(
                start_utc_ns=1795698741646842557,
                recovery_utc_ns=None,
                evidence=[
                    dict(
                        path="observed/master_events_master.jsonl", available_at=1795698741646842558
                    )
                ],
            )
        ],
    )
    labels.write_text(json.dumps(value) + "\n")
    (run / "manifest.json").write_text('{"run_id": "run_fixture", "status": "completed"}')
    repository = ExperimentRepository(tmp_path)
    key = next(iter(repository.run_directories(identity)))
    page = repository.labels(identity, key, 0, 10)
    assert page.total == 1
    finding = page.labels[0]["findings"][0]
    assert finding["start_utc_ns"] == "1795698741646842557"
    assert finding["evidence"][0]["available_at"] == "1795698741646842558"
    assert page.labels[0]["fault_applied"] is True
    assert page.labels[0]["service_violation"] is False
    assert repository.labels(identity, key, 1, 10).labels == []
    with pytest.raises(FileNotFoundError):
        repository.labels(identity, "unknown", 0, 10)


def test_pair_submission_uses_only_server_frozen_matching_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).resolve().parents[2]
    monkeypatch.setenv(
        "PVCOMM_SIMILARITY_POLICY", str(source / "configs/v0.4-similarity-policy.json")
    )
    monkeypatch.setenv(
        "PVCOMM_SIMILARITY_CALIBRATION",
        str(source / "configs/v0.4-similarity-calibration-dev.json"),
    )
    client = TestClient(create_app(tmp_path / "m2", tmp_path / "requests"))
    assert client.get("/api/diagnostics/experiments/catalog").json()["pair_submission_enabled"]
    response = client.post("/api/diagnostics/experiments", json=body(template="P04"))
    assert response.status_code == 201
    identity = response.json()["experiment_id"]
    matrix = BatchConfig.model_validate_json(
        (tmp_path / "requests" / identity / "matrix.json").read_bytes()
    )
    assert matrix.matching is not None
    task = matrix.tasks[0]
    assert task.kind == "pair" and task.pair.normal.simulation.protocol.seed == 90123
    assert task.pair.changed.background.simulation.protocol.seed == 90123
