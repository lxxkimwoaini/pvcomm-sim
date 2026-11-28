"""Real API submission -> authorized worker -> c104 evidence -> read-only diagnostics."""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pvcomm.scenario.requests import execute_requests
from pvcomm.web.app import create_app


@pytest.mark.lab
def test_real_submitted_request_and_quality(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires explicitly authorized Linux lab")
    root = tmp_path / "experiments"
    client = TestClient(create_app(tmp_path / "m2", root))
    assert client.get("/api/diagnostics/experiments/catalog").json()["site_counts"] == [3, 10]
    assert (
        client.post(
            "/api/diagnostics/experiments",
            json=dict(template="N09", seed=902001, site_count=30),
        ).status_code
        == 422
    )
    assert not root.exists()
    response = client.post("/api/diagnostics/experiments", json=dict(template="N09", seed=902001))
    assert response.status_code == 201
    identity = response.json()["experiment_id"]
    assert response.json()["status"] == "queued"
    assert execute_requests(root) == [identity]
    final = client.get(f"/api/diagnostics/experiments/{identity}").json()
    assert final["status"] == "completed"
    assert final["queue"]["coverage"]["valid_normal_runs"] == 1
    assert "artifacts" not in json.dumps(final)
    reports = client.get(f"/api/diagnostics/experiments/{identity}/quality").json()
    assert len(reports["runs"]) == 1
    run = reports["runs"][0]
    assert run["status"] == "completed" and not run["cleanup_errors"]
    assert run["reports"] and all(r["passed"] for r in run["reports"])
    labels = client.get(
        f"/api/diagnostics/experiments/{identity}/runs/{run['run_key']}/labels"
    ).json()
    assert labels["total"] == 3
    assert all(row["label_status"] == "valid" for row in labels["labels"])
    assert not any(row["service_violation"] for row in labels["labels"])
    assert execute_requests(root) == []
    assert client.get("/api/runs").json() == {"runs": []}
    (tmp_path / "browser-reference.json").write_text(
        json.dumps(dict(experiment_id=identity, experiment_root=str(root))), encoding="utf-8"
    )
