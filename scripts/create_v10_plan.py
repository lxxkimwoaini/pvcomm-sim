"""Freeze three-site production jobs using the existing scenario and batch contracts."""

import argparse
from pathlib import Path
from typing import Any

from pvcomm.config.batch import BatchConfig, BatchJob, FaultJob, NormalJob
from pvcomm.config.fault_scenes import a01_scenario, a02_scenario, terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, StationScope
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.scenes import ScenarioConfig
from pvcomm.pipeline.dataset import code_record, read_json, write_json
from pvcomm.storage.parquet import iter_jsonl, source_hash

TEST_DATES = ("2024-06-27", "2024-06-28", "2024-06-29")
FAULTS = ("A01", "A02", "A04", "A05", "A06", "A08")


def source(scene: dict[str, Any], date: str, clock: str, seed: int) -> None:
    simulation = scene["simulation"]
    simulation["weather"]["source_start"] = f"{date}T{clock}:00Z"
    scene["source_group_id"] = (
        f"weather_{simulation['weather']['sha256'][:12]}_{date.replace('-', '')}"
    )
    simulation["protocol"]["seed"] = seed
    for station in simulation["protocol"]["stations"]:
        station["access_link"]["seed"] += seed
    for region in simulation["regions"]:
        region["uplink"]["seed"] += seed


def fault_job(code: str, date: str, index: int) -> FaultJob:
    scope = StationScope(kind="station", station_ids=("PV01",))
    template = (
        a01_scenario(scope, 60)
        if code == "A01"
        else a02_scenario(scope, 3000, "rtu_to_master")
        if code == "A02"
        else terminal_scenario(code, scope, {"A04": 12, "A05": 15, "A06": 60, "A08": 3}[code])
    )
    raw = template.model_dump(mode="json")
    background = raw["background"]
    simulation = background["simulation"]
    # Shift the accepted case intact, leaving >60s post-warmup real history.
    simulation["protocol"]["duration_s"] += 80
    raw["fault"]["start_s"] += 80
    for task in background["business_schedule"]:
        task["at_s"] += 80
    if simulation["protocol"]["interruption"]:
        simulation["protocol"]["interruption"]["start_s"] += 80
    cloud = simulation["weather"].get("cloud_shadow")
    if cloud:
        cloud["center_s"] += 80
    source(
        background,
        date,
        ("04:00", "11:30", "16:00")[index],
        1_000_000 + index * 100 + FAULTS.index(code),
    )
    scenario = FaultScenarioConfig.model_validate(raw)
    return FaultJob(
        kind="fault",
        task_id=f"test_{date.replace('-', '')}_{code}",
        max_attempts=2,
        scenario=scenario,
    )


def normal_job(index: int) -> NormalJob:
    code = ("N01", "N03", "N02", "N07", "N09", "N10")[index % 6]
    raw = normal_scenario(code).model_dump(mode="json")
    simulation = raw["simulation"]
    simulation["protocol"].update(duration_s=1802, interruption=None)
    simulation["device_operations"] = []
    source(raw, "2024-06-12", f"{index // 2:02d}:{30 * (index % 2):02d}", 2_000_000 + index)
    raw["business_schedule"] = [
        dict(
            kind="interrogation",
            schedule_id="daily_gi",
            at_s=180,
            station_ids=["PV01", "PV02", "PV03"],
        ),
        dict(
            kind="setpoint",
            schedule_id="limit",
            at_s=360,
            station_ids=["PV01"],
            targets_kw={"PV01": 8.0},
        ),
        dict(
            kind="setpoint",
            schedule_id="release",
            at_s=900,
            station_ids=["PV01"],
            targets_kw={"PV01": 30.0},
        ),
    ]
    if code == "N09":
        simulation["device_operations"] = [
            dict(station_id="PV01", at_s=350, enabled=False),
            dict(station_id="PV01", at_s=390, enabled=True),
        ]
    if code == "N10":
        simulation["protocol"]["interruption"] = dict(
            station_id="PV02", start_s=1200, duration_s=20, planned=True
        )
    if code == "N03":
        simulation["weather"]["cloud_shadow"]["center_s"] = 600.0
    return NormalJob(
        kind="normal",
        task_id=f"normal_20240612_{index:02d}",
        max_attempts=2,
        scenario=ScenarioConfig.model_validate(raw),
    )


def test_normal_job(date: str, index: int) -> NormalJob:
    raw = normal_scenario("N10" if index % 2 == 0 else "N09").model_dump(mode="json")
    raw["simulation"]["protocol"]["duration_s"] = 180
    source(raw, date, "21:00", 3_000_000 + index)
    return NormalJob(
        kind="normal",
        task_id=f"test_normal_{date.replace('-', '')}",
        max_attempts=2,
        scenario=ScenarioConfig.model_validate(raw),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    args = parser.parse_args()
    previous = [r for r in iter_jsonl([args.development / "runs.jsonl"]) if r.get("eligible")]
    used = {r["weather"]["source_start"][:10] for r in previous}
    if used.intersection(TEST_DATES) or len(previous) != 106:
        raise ValueError("development provenance differs from the frozen 106-run basis")
    args.output.mkdir(parents=True, exist_ok=False)
    test_jobs: list[BatchJob] = []
    for index, date in enumerate(TEST_DATES):
        test_jobs.extend(fault_job(code, date, index) for code in FAULTS)
        test_jobs.append(test_normal_job(date, index))
    batches = {
        "independent_test": BatchConfig(
            matrix_id="v10_independent_test",
            tasks=tuple(test_jobs),
        ),
        "normal_24h": BatchConfig(
            matrix_id="v10_normal_24h",
            tasks=tuple(normal_job(i) for i in range(48)),
            maximum_output_bytes=100 * 1024**3,
        ),
    }
    for name, batch in batches.items():
        write_json(args.output / f"{name}.json", batch.model_dump(mode="json"))
    write_json(
        args.output / "protocol.json",
        dict(
            version="1.0",
            **code_record(),
            test_dates=TEST_DATES,
            development_dataset=str(args.development),
            development_release_sha256=source_hash(args.development / "release.json"),
            development_assignment=read_json(args.development / "split/assignment.json"),
            development_policy=(
                "prior validation stays validation; prior train/test become train; "
                "ten-site diagnostics retained separately"
            ),
            independent_test_policy=(
                "all new held-out dates remain test regardless of labels or model scores"
            ),
            windows={"W10": [10, 1], "W30": [30, 1], "W60": [60, 5]},
            test_evaluation="QC and coverage only; no model fitting or benchmark on formal test",
            warmup_note=(
                "faults shifted 80s intact; minimum pre-fault history exceeds 60s excluding warmup"
            ),
            normal_target_hours=168,
            first_stage_hours=24,
            segment_business_seconds=1800,
            normal_segments=(
                "48 independent initialized segments, not uninterrupted communication; "
                "subsequent 144h not launched automatically"
            ),
            batch_sha256={name: source_hash(args.output / f"{name}.json") for name in batches},
            inherited_purpose_literals=(
                "legacy development literals retained for schema compatibility; "
                "this protocol identifies production use"
            ),
        ),
    )
    print(f"Frozen 18 fault jobs and 48 half-hour normal jobs in {args.output}")


if __name__ == "__main__":
    main()
