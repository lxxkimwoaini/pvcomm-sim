"""Real serial queue composition and completed-task resume without rerunning c104."""

import json
import os
import sys
from pathlib import Path

import pytest

from pvcomm.config.batch import BatchConfig
from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import RegionScope
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.scenario.batch import run_batch


@pytest.mark.lab
def test_real_finite_batch_and_verified_resume(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires authorized isolated Linux and historical inputs")
    fault = terminal_scenario("A08", RegionScope(kind="region", region_id="R1"), 3)
    config = BatchConfig.model_validate(
        dict(
            matrix_id="queue_acceptance",
            tasks=[
                dict(kind="normal", task_id="task_1", scenario=normal_scenario("N09")),
                dict(kind="fault", task_id="task_2", scenario=fault),
            ],
        )
    )
    output = tmp_path / "queue"
    state = run_batch(config, output)
    assert state["status"] == "completed"
    assert state["coverage"]["completed_tasks"] == 2
    assert state["coverage"]["valid_fault_runs"] == {"A08": 1}
    assert state["coverage"]["valid_normal_runs"] == 1
    assert len(set(state["coverage"]["independent_run_ids"])) == 2
    snapshots = [task["attempts"] for task in state["tasks"]]
    resumed = run_batch(config, output, resume=True)
    assert resumed["status"] == "completed" and resumed["coverage"] == state["coverage"]
    assert [task["attempts"] for task in resumed["tasks"]] == snapshots
    for task in resumed["tasks"]:
        attempt = task["attempts"][0]
        assert attempt["retained_bytes"] > 0 and attempt["artifacts"]
        root = output / attempt["directory"] / attempt["result"]["root"]
        manifest = json.loads((root / "manifest.json").read_bytes())
        assert manifest["status"] == "completed" and not manifest["cleanup_errors"]
        assert all(c["statistics"]["packets_dropped_by_kernel"] == 0 for c in manifest["captures"])
