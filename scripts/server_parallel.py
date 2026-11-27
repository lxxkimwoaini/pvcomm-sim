"""Small outer driver for independent, unchanged serial laboratory batches.

Deploy outside the frozen application checkout. No protocol/model/QC changes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIB = 1024**3
TRIAL_INDICES = (5, 29)


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextlib.contextmanager
def locked(path: Path, *, blocking: bool = False) -> Iterator[None]:
    import fcntl

    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def source_record(application: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(application), *args], text=True).strip()

    return dict(
        code_version=git("rev-parse", "HEAD"), code_dirty=bool(git("status", "--porcelain"))
    )


def build_jobs(date: str) -> list[Any]:
    import pvcomm
    from pvcomm.config.batch import NormalJob

    template = runpy.run_path(str(Path(pvcomm.__file__).parents[2] / "scripts/create_v10_plan.py"))[
        "normal_job"
    ]
    jobs = []
    for i in range(48):
        raw = template(i).model_dump(mode="json")
        scene = raw["scenario"]
        simulation = scene["simulation"]
        protocol = simulation["protocol"]
        old_seed, new_seed = protocol["seed"], 5_000_000 + i
        protocol["seed"] = new_seed
        for station in protocol["stations"]:
            station["access_link"]["seed"] += new_seed - old_seed
        for region in simulation["regions"]:
            region["uplink"]["seed"] += new_seed - old_seed
        simulation["weather"]["source_start"] = f"{date}T{i // 2:02d}:{30 * (i % 2):02d}:00Z"
        scene["source_group_id"] = (
            f"weather_{simulation['weather']['sha256'][:12]}_{date.replace('-', '')}"
        )
        raw["task_id"] = f"server_normal_{date.replace('-', '')}_{i:02d}"
        jobs.append(NormalJob.model_validate(raw))
    return jobs


def freeze(args: argparse.Namespace) -> None:
    from pvcomm.config.batch import BatchConfig

    audit = read(args.audit)
    if (
        audit["errors"]
        or args.date in audit["dates"]
        or args.date in ("2024-06-27", "2024-06-28", "2024-06-29")
    ):
        raise ValueError("date unavailable or provenance audit incomplete")
    code = source_record(args.application)
    if code["code_dirty"]:
        raise ValueError("application must remain clean and frozen")
    jobs = build_jobs(args.date)
    remaining = [j for i, j in enumerate(jobs) if i not in TRIAL_INDICES]
    groups = {"trial_a": [jobs[5]], "trial_b": [jobs[29]]}
    groups.update({f"worker_{i + 1}": remaining[i::4] for i in range(4)})
    args.plan.mkdir(parents=True, exist_ok=False)
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.audit, args.plan / "date-audit.json")
    workers = []
    for name, tasks in groups.items():
        batch = BatchConfig(
            matrix_id=f"server_{args.date.replace('-', '')}_{name}",
            tasks=tuple(tasks),
            maximum_output_bytes=100 * GIB,
        )
        path = args.plan / f"{name}.json"
        save(path, batch.model_dump(mode="json"))
        workers.append(
            dict(
                worker_id=name,
                config=str(path),
                config_sha256=digest(path),
                output=str(args.output / name),
                task_ids=[j.task_id for j in tasks],
            )
        )
    save(args.plan / "tasks.json", [j.model_dump(mode="json") for j in jobs])
    save(
        args.plan / "protocol.json",
        dict(
            version="server-parallel-normal-v1",
            created_at=now(),
            **code,
            application=str(args.application),
            output=str(args.output),
            driver=str(Path(__file__).resolve()),
            driver_sha256=digest(Path(__file__)),
            task_list_sha256=digest(args.plan / "tasks.json"),
            date_audit_sha256=digest(args.plan / "date-audit.json"),
            date=args.date,
            source_group_id=jobs[0].scenario.source_group_id,
            partition="train",
            segment_count=48,
            duration_s=1802,
            warmup_s=2,
            effective_hours=24,
            continuity="48 independently initialized half-hour segments, not continuous 24h",
            maximum_workers=4,
            minimum_available_memory_bytes=16 * GIB,
            safety_bytes=20 * GIB,
            prerequisite=str(args.prerequisite),
            namespace_wrapper=str(Path.home() / "pvcomm-tools/run_userns_lab.sh"),
            namespace_wrapper_sha256=digest(Path.home() / "pvcomm-tools/run_userns_lab.sh"),
            workers=workers,
            budget_basis="remaining attempts with retention margin plus parser allowance",
            inherited_purpose="development literals retained for existing schema compatibility",
        ),
    )
    print(json.dumps(read(args.plan / "protocol.json"), indent=2))


def verify(plan: Path) -> dict[str, Any]:
    protocol = read(plan / "protocol.json")
    if source_record(Path(protocol["application"])) != {
        "code_version": protocol["code_version"],
        "code_dirty": False,
    }:
        raise ValueError("frozen application version/clean state changed")
    files = [
        (Path(protocol["driver"]), protocol["driver_sha256"]),
        (Path(protocol["namespace_wrapper"]), protocol["namespace_wrapper_sha256"]),
        (plan / "tasks.json", protocol["task_list_sha256"]),
        (plan / "date-audit.json", protocol["date_audit_sha256"]),
    ]
    files.extend((Path(w["config"]), w["config_sha256"]) for w in protocol["workers"])
    if any(digest(p) != sha for p, sha in files):
        raise ValueError("frozen driver/configuration/audit changed")
    return dict(protocol)


def remaining_attempts(task: dict[str, Any], maximum: int) -> int:
    if task["status"] == "completed":
        return 0
    active = sum(a["status"] == "running" for a in task["attempts"])
    return int(max(0, maximum - len(task["attempts"]) + active))


def budget(protocol: dict[str, Any], concurrency: int) -> dict[str, Any]:
    from pvcomm.config.batch import BatchConfig
    from pvcomm.scenario.batch import duration_s, projected_retained_bytes

    retained, temporary = 0, 0
    for worker in protocol["workers"]:
        config = BatchConfig.model_validate(read(Path(worker["config"])))
        summary = Path(worker["output"]) / "summary.json"
        state = {t["task_id"]: t for t in read(summary)["tasks"]} if summary.exists() else {}
        for task in config.tasks:
            attempts = (
                remaining_attempts(state[task.task_id], task.max_attempts)
                if task.task_id in state
                else task.max_attempts
            )
            retained += projected_retained_bytes(config, duration_s(task) * attempts)
        temporary = max(temporary, config.retention.measured_parse_temporary_bytes)
    memory = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
    }
    required = retained + temporary * concurrency + protocol["safety_bytes"]
    free = shutil.disk_usage(protocol["output"]).free
    available = memory["MemAvailable"]
    return dict(
        at=now(),
        concurrency=concurrency,
        free_bytes=free,
        remaining_retained_budget_bytes=retained,
        temporary_allowance_bytes=temporary * concurrency,
        safety_bytes=protocol["safety_bytes"],
        required_bytes=required,
        mem_available_bytes=available,
        disk_ok=free >= required,
        memory_ok=available >= protocol["minimum_available_memory_bytes"],
        allowed=free >= required and available >= protocol["minimum_available_memory_bytes"],
    )


def namespaces(pid: int) -> dict[str, str]:
    return {name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("user", "mnt", "net")}


def worker_run(plan: Path, name: str) -> None:
    from pvcomm.config.batch import BatchConfig
    from pvcomm.scenario import batch
    from pvcomm.scenario.batch_resources import BatchResources

    protocol = verify(plan)
    worker = next(w for w in protocol["workers"] if w["worker_id"] == name)
    output = Path(worker["output"])
    config = BatchConfig.model_validate(read(Path(worker["config"])))
    original = batch.execute_job

    def admitted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Only this driver process gains an admission boundary; run_batch stays unchanged.
        while True:
            with locked(plan / ".admission.lock", blocking=True):
                check = budget(
                    protocol,
                    2
                    if name.startswith("trial")
                    else read(plan / "parallel-index.json")["production_concurrency"],
                )
                save(plan / f"{name}-admission.json", check)
                if check["allowed"]:
                    break
            time.sleep(10)
        result = original(*args, **kwargs)
        check = audit_run(Path(args[1]) / result["root"])
        result["strict_parallel_gate"] = check
        result["accepted"] = bool(result["accepted"] and check["passed"])
        return result

    with locked(plan / f".{name}.driver.lock"):
        began = time.monotonic()
        save(
            plan / f"{name}-identity.json",
            dict(
                pid=os.getpid(),
                namespaces=namespaces(os.getpid()),
                started_at=now(),
                config_sha256=worker["config_sha256"],
            ),
        )
        batch.execute_job = admitted
        try:
            measurement = plan / f"{name}-resources-{time.time_ns()}"
            with BatchResources(output, measurement):
                result = batch.run_batch(config, output, resume=(output / "manifest.json").exists())
        finally:
            batch.execute_job = original
        save(
            plan / f"{name}-result.json",
            dict(
                ended_at=now(),
                elapsed_s=time.monotonic() - began,
                status=result["status"],
                coverage=result["coverage"],
            ),
        )
        if result["status"] != "completed":
            raise RuntimeError(f"worker ended with {result['status']}")


def audit_run(root: Path) -> dict[str, Any]:
    manifest = read(root / "manifest.json")
    reports = [read(p) for p in sorted((root / "qc").glob("*/report.json"))]
    normal = next(r for r in reports if "scheduled_business" in r)
    label = next(r for r in reports if "invalid_labels" in r)
    quality = normal["experiment_quality"]["data_quality"]["checks"]
    apdu = quality["complete_apdu_parse_rate"]["evidence"]
    captures = manifest["captures"]
    drops = [c["statistics"].get("packets_dropped_by_kernel") for c in captures]
    passed = bool(
        normal["passed"]
        and label["passed"]
        and label["invalid_labels"] == 0
        and label["uncertain_labels"] == 0
        and not label["service_violation_stations"]
        and apdu["confirmed_complete_apdus"] > 0
        and apdu["matched"] == apdu["confirmed_complete_apdus"]
        and apdu["rate"] == 1
        and not apdu["incomplete_bytes"]
        and len(drops) == 4
        and drops == [0] * 4
        and manifest["cleanup_errors"] == []
        and not manifest["code_dirty"]
    )
    return dict(
        run_id=manifest["run_id"],
        root=str(root),
        passed=passed,
        apdu=apdu,
        capture_drops=drops,
        invalid_labels=label["invalid_labels"],
        uncertain_labels=label["uncertain_labels"],
        service_violation_stations=label["service_violation_stations"],
        cleanup_errors=manifest["cleanup_errors"],
    )


def audit_output(output: Path) -> dict[str, Any]:
    """Read independent QC reports and rehash every batch-inventoried artifact."""
    from pvcomm.config.batch import BatchConfig
    from pvcomm.scenario.batch import validate_resume

    state = read(output / "manifest.json")
    config = BatchConfig.model_validate(read(output / "matrix.json"))
    validate_resume(config, output, state)
    runs = []
    errors = []
    for task in state["tasks"]:
        good = [a for a in task["attempts"] if a.get("result", {}).get("accepted")]
        if task["status"] != "completed" or len(good) != 1:
            errors.append(f"{task['task_id']}: no unique completed attempt")
            continue
        attempt = good[0]
        root = output / attempt["directory"] / attempt["result"]["root"]
        check = audit_run(root)
        runs.append(
            dict(
                task_id=task["task_id"],
                **check,
                artifact_hashes_verified=len(attempt["artifacts"]),
                elapsed_s=attempt["elapsed_s"],
                retained_bytes=attempt["retained_bytes"],
            )
        )
        if not check["passed"]:
            errors.append(f"{task['task_id']}: strict production gate failed")
    return dict(
        checked_at=now(),
        passed=state["status"] == "completed" and not errors,
        completed_runs=len(runs),
        effective_hours=len([r for r in runs if r["passed"]]) * 0.5,
        errors=errors,
        runs=runs,
    )


def identity_alive(record: dict[str, Any]) -> bool:
    try:
        return bool(
            Path(f"/proc/{record['pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
            == record["start_ticks"]
        )
    except FileNotFoundError:
        return False


def launch(protocol: dict[str, Any], plan: Path, worker: dict[str, Any]) -> dict[str, Any]:
    name = worker["worker_id"]
    env = dict(os.environ)
    temporary = Path(protocol["output"]) / f"{name}-tmp"
    temporary.mkdir(exist_ok=True)
    env["TMPDIR"] = str(temporary)
    command = [
        "sh",
        protocol["namespace_wrapper"],
        str(Path(protocol["application"]) / ".venv/bin/python"),
        "-u",
        protocol["driver"],
        "worker",
        "--plan",
        str(plan),
        "--worker",
        name,
    ]
    with (plan / f"{name}.log").open("ab") as log:
        child = subprocess.Popen(
            command,
            cwd=protocol["application"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return dict(
        pid=child.pid,
        start_ticks=Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
        started_at=now(),
        command=command,
        launches=1,
    )


def isolated(plan: Path, names: list[str]) -> bool:
    identities = [read(plan / f"{name}-identity.json") for name in names]
    return all(
        len({i["namespaces"][key] for i in identities}) == len(names)
        for key in ("net", "mnt", "user")
    )


def resume_index(index: dict[str, Any]) -> None:
    """Requeue stopped drivers only; the original batch retains its retry ledger."""
    for worker in index["workers"].values():
        if worker["status"] not in ("running", "failed"):
            continue
        if "pid" in worker and identity_alive(worker):
            continue
        worker.setdefault("previous_launches", []).append(
            {
                k: worker[k]
                for k in ("pid", "started_at", "ended_at", "exit_code", "error")
                if k in worker
            }
        )
        for key in ("exit_code", "error", "ended_at", "audit"):
            worker.pop(key, None)
        worker["status"] = "queued"
    if index["status"] in ("completed_with_failures", "trial_failed_no_production_started"):
        index["status"] = "production" if index["production_concurrency"] else "two_worker_trial"


def control(plan: Path, *, resume: bool = False) -> None:
    from pvcomm.clock import ClockOrigin
    from pvcomm.scenario.batch_resources import descendants
    from pvcomm.scenario.resources import ResourceSampler

    protocol = verify(plan)
    index_path = plan / "parallel-index.json"
    index = (
        read(index_path)
        if index_path.exists()
        else dict(
            version=protocol["version"],
            code_version=protocol["code_version"],
            code_dirty=False,
            original_task_list_sha256=protocol["task_list_sha256"],
            started_at=now(),
            workers={w["worker_id"]: dict(**w, status="queued") for w in protocol["workers"]},
            production_concurrency=0,
            status="waiting_prerequisite",
        )
    )
    resources = plan / f"coordinator-resources-{time.time_ns()}"
    resources.mkdir()
    sampler = ResourceSampler(
        resources, ClockOrigin.capture(), scope="parallel_controller_and_workers"
    )
    with locked(plan / ".coordinator.lock"):
        if resume:
            resume_index(index)
        while True:
            for worker in index["workers"].values():
                if worker["status"] != "running":
                    continue
                # Reap owned children before /proc liveness (zombies still have /proc entries).
                with contextlib.suppress(ChildProcessError):
                    waited, status = os.waitpid(worker["pid"], os.WNOHANG)
                    if waited:
                        worker["exit_code"] = os.waitstatus_to_exitcode(status)
                if "exit_code" not in worker and identity_alive(worker):
                    summary = Path(worker["output"]) / "summary.json"
                    if summary.exists():
                        worker["actual"] = read(summary)
                    continue
                worker["ended_at"] = now()
                try:
                    worker["actual"] = read(Path(worker["output"]) / "summary.json")
                    audit = audit_output(Path(worker["output"]))
                    save(plan / f"{worker['worker_id']}-audit.json", audit)
                    worker["audit"] = audit
                    worker["status"] = "completed" if audit["passed"] else "failed"
                    result_file = plan / f"{worker['worker_id']}-result.json"
                    if result_file.exists():
                        worker["execution"] = read(result_file)
                except Exception as exc:
                    worker.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            if index["status"] == "waiting_prerequisite":
                prerequisite = Path(protocol["prerequisite"])
                summary = read(prerequisite / "summary.json")
                if summary["status"] != "running":
                    if summary["status"] != "completed":
                        raise RuntimeError("existing one-hour task did not complete successfully")
                    audit = audit_output(prerequisite)
                    # Prerequisite is one full-hour run, not one new half-hour segment.
                    audit.pop("effective_hours")
                    save(plan / "prerequisite-audit.json", audit)
                    if not audit["passed"]:
                        raise RuntimeError("existing one-hour task failed independent gate")
                    index["status"] = "two_worker_trial"
            trials = [w for n, w in index["workers"].items() if n.startswith("trial")]
            production = [w for n, w in index["workers"].items() if n.startswith("worker")]
            if index["status"] == "two_worker_trial":
                check = budget(protocol, 2)
                if any(w["status"] == "failed" for w in trials):
                    index["status"] = "trial_failed_no_production_started"
                elif all(w["status"] == "completed" for w in trials):
                    if not isolated(plan, [w["worker_id"] for w in trials]):
                        raise RuntimeError("trial namespace isolation failed")
                    index["status"] = "production"
                    check4 = budget(protocol, 4)
                    index["four_worker_start_budget"] = check4
                    index["production_concurrency"] = 4 if check4["allowed"] else 2
                elif check["allowed"]:
                    for worker in trials:
                        if worker["status"] == "queued":
                            worker.update(launch(protocol, plan, worker), status="running")
            if index["status"] == "production":
                concurrency = index["production_concurrency"]
                check = budget(protocol, concurrency)
                running = sum(w["status"] == "running" for w in production)
                for worker in production:
                    if running >= concurrency or not check["allowed"]:
                        break
                    if worker["status"] == "queued":
                        # Publish concurrency before a worker reaches its admission boundary.
                        save(index_path, index)
                        worker.update(launch(protocol, plan, worker), status="running")
                        running += 1
                        check = budget(protocol, concurrency)
                if all(w["status"] in ("completed", "failed") for w in production):
                    index["status"] = (
                        "completed"
                        if all(w["status"] == "completed" for w in production)
                        else "completed_with_failures"
                    )
                    index["ended_at"] = now()
            index["resource_admission"] = budget(protocol, max(2, index["production_concurrency"]))
            for group in (trials, production):
                if len(group) >= 2 and all(
                    (plan / f"{w['worker_id']}-identity.json").exists() for w in group
                ):
                    index[f"{group[0]['worker_id']}_namespace_isolation"] = isolated(
                        plan, [w["worker_id"] for w in group]
                    )
            index["updated_at"] = now()
            index["coordinator_pid"] = os.getpid()
            index["valid_runs"] = sum(
                w.get("actual", {}).get("coverage", {}).get("completed_tasks", 0)
                for w in index["workers"].values()
            )
            index["effective_normal_hours"] = index["valid_runs"] / 2
            save(index_path, index)
            sampler.sample(descendants(os.getpid()))
            with (resources / "admission.jsonl").open("a") as stream:
                stream.write(json.dumps(index["resource_admission"]) + "\n")
            if index["status"] in (
                "completed",
                "completed_with_failures",
                "trial_failed_no_production_started",
            ):
                return
            time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "control", "worker", "audit"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--application", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--prerequisite", type=Path)
    parser.add_argument("--date", default="2024-06-14")
    parser.add_argument("--worker")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.mode == "freeze":
        freeze(args)
    elif args.mode == "worker":
        worker_run(args.plan, args.worker)
    elif args.mode == "audit":
        print(json.dumps(audit_output(args.output), indent=2))
    else:
        control(args.plan, resume=args.resume)


if __name__ == "__main__":
    main()
