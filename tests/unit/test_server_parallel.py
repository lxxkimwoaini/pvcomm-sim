"""Outer-driver bookkeeping fixtures; never presented as real parallel evidence."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def driver() -> Any:
    spec = importlib.util.spec_from_file_location("server_parallel", "scripts/server_parallel.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_day_preserves_business_and_distinct_seeds() -> None:
    module = driver()
    jobs = module.build_jobs("2024-06-14")
    assert len(jobs) == len({j.task_id for j in jobs}) == 48
    assert {j.scenario.normal_scene for j in jobs} == {"N01", "N02", "N03", "N07", "N09", "N10"}
    assert len({j.scenario.source_group_id for j in jobs}) == 1
    assert len({j.scenario.simulation.protocol.seed for j in jobs}) == 48
    assert (
        sum(
            j.scenario.simulation.protocol.duration_s - j.scenario.simulation.protocol.warmup_s
            for j in jobs
        )
        == 86400
    )
    assert {j.scenario.simulation.weather.source_start.date().isoformat() for j in jobs} == {
        "2024-06-14"
    }
    assert all(len(j.scenario.simulation.protocol.stations) == 3 for j in jobs)
    assert all(
        [b.schedule_id for b in j.scenario.business_schedule] == ["daily_gi", "limit", "release"]
        for j in jobs
    )
    remaining = [j for i, j in enumerate(jobs) if i not in module.TRIAL_INDICES]
    assert [len(remaining[i::4]) for i in range(4)] == [12, 12, 11, 11]
    assert jobs[5].scenario.simulation.protocol.interruption.planned
    assert (
        jobs[5].scenario.simulation.weather.source_start
        != jobs[29].scenario.simulation.weather.source_start
    )


@pytest.mark.parametrize(
    ("status", "attempts", "expected"),
    [
        ("queued", [], 2),
        ("running", [{"status": "running"}], 2),
        ("queued", [{"status": "failed"}], 1),
        ("running", [{"status": "failed"}, {"status": "running"}], 1),
        ("completed", [{"status": "completed"}], 0),
        ("failed", [{"status": "failed"}, {"status": "failed"}], 0),
    ],
)
def test_global_budget_includes_active_attempt_and_bounded_retries(
    status: str, attempts: list[dict[str, str]], expected: int
) -> None:
    assert driver().remaining_attempts(dict(status=status, attempts=attempts), 2) == expected


def test_used_or_reserved_day_is_rejected_before_creating_output(tmp_path: Path) -> None:
    from argparse import Namespace

    module = driver()
    audit = tmp_path / "audit.json"
    for date, records in (
        ("2024-06-14", {"2024-06-14": ["failed/input.json"]}),
        ("2024-06-27", {}),
    ):
        module.save(audit, dict(errors=[], dates=records))
        args = Namespace(audit=audit, date=date, plan=tmp_path / "plan", output=tmp_path / "output")
        with pytest.raises(ValueError, match="date unavailable"):
            module.freeze(args)
        assert not args.output.exists() and not args.plan.exists()


def test_frozen_script_hash_is_checked_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = driver()
    module.save(
        tmp_path / "protocol.json",
        dict(
            application="unit",
            code_version="sha",
            driver=str(tmp_path / "driver.py"),
            driver_sha256="wrong",
            namespace_wrapper=str(tmp_path / "driver.py"),
            namespace_wrapper_sha256="wrong",
            task_list_sha256="wrong",
            date_audit_sha256="wrong",
            workers=[],
        ),
    )
    (tmp_path / "driver.py").write_text("# modified fixture")
    monkeypatch.setattr(
        module, "source_record", lambda _: dict(code_version="sha", code_dirty=False)
    )
    with pytest.raises(ValueError, match="changed"):
        module.verify(tmp_path)


def test_one_global_budget_reserves_all_workers_and_four_parsers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from pvcomm.config.batch import BatchConfig

    module = driver()
    jobs = module.build_jobs("2024-06-14")
    workers = []
    for i in range(4):
        config = tmp_path / f"w{i}.json"
        module.save(
            config, BatchConfig(matrix_id=f"unit_{i}", tasks=(jobs[i],)).model_dump(mode="json")
        )
        workers.append(dict(config=str(config), output=str(tmp_path / f"output_{i}")))
    protocol = dict(
        workers=workers,
        output=str(tmp_path),
        safety_bytes=20 * module.GIB,
        minimum_available_memory_bytes=16 * module.GIB,
    )
    original = Path.read_text

    def memory(path: Path, *args: Any, **kwargs: Any) -> str:
        return (
            "MemAvailable: 33554432 kB\n"
            if str(path) == "/proc/meminfo"
            else original(path, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "read_text", memory)
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=200 * module.GIB)
    )
    check = module.budget(protocol, 4)
    assert check["allowed"]
    assert check["temporary_allowance_bytes"] == 4 * 11_275_084_964
    assert (
        check["required_bytes"]
        == check["remaining_retained_budget_bytes"] + 4 * 11_275_084_964 + 20 * module.GIB
    )
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=check["required_bytes"] - 1)
    )
    assert not module.budget(protocol, 4)["allowed"]
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=200 * module.GIB)
    )
    monkeypatch.setattr(Path, "read_text", lambda _: "MemAvailable: 8388608 kB\n")
    assert not module.budget(protocol, 4)["allowed"]


@pytest.mark.parametrize("defect", ["none", "drop", "parse", "uncertain", "violation", "cleanup"])
def test_strict_production_gate_rejects_bad_evidence(tmp_path: Path, defect: str) -> None:
    module = driver()
    normal = tmp_path / "qc/normal/report.json"
    labels = tmp_path / "qc/labels/report.json"
    normal.parent.mkdir(parents=True)
    labels.parent.mkdir(parents=True)
    module.save(
        tmp_path / "manifest.json",
        dict(
            run_id="unit_synthetic_only",
            code_dirty=False,
            cleanup_errors=["failure"] if defect == "cleanup" else [],
            captures=[dict(statistics=dict(packets_dropped_by_kernel=int(defect == "drop")))] * 4,
        ),
    )
    module.save(
        normal,
        dict(
            passed=True,
            scheduled_business={},
            experiment_quality=dict(
                data_quality=dict(
                    checks=dict(
                        complete_apdu_parse_rate=dict(
                            evidence=dict(
                                confirmed_complete_apdus=100,
                                matched=99 if defect == "parse" else 100,
                                rate=0.99 if defect == "parse" else 1,
                                incomplete_bytes=0,
                            )
                        )
                    )
                )
            ),
        ),
    )
    module.save(
        labels,
        dict(
            passed=True,
            invalid_labels=0,
            uncertain_labels=int(defect == "uncertain"),
            service_violation_stations=["PV01"] if defect == "violation" else [],
        ),
    )
    assert module.audit_run(tmp_path)["passed"] == (defect == "none")


def test_resume_retains_live_workers_and_prior_failure_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = driver()
    monkeypatch.setattr(module, "identity_alive", lambda r: r["pid"] == 10)
    state: dict[str, Any] = dict(
        status="completed_with_failures",
        production_concurrency=4,
        workers=dict(
            active=dict(status="running", pid=10),
            stopped=dict(status="failed", pid=20, exit_code=1, error="actual failure"),
            complete=dict(status="completed", pid=30),
        ),
    )
    module.resume_index(state)
    assert state["workers"]["active"]["status"] == "running"
    assert state["workers"]["complete"]["status"] == "completed"
    stopped = state["workers"]["stopped"]
    assert stopped["status"] == "queued" and "exit_code" not in stopped
    assert stopped["previous_launches"][0]["error"] == "actual failure"
    assert state["status"] == "production"
