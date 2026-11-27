"""Production configuration guards only; these are not simulation evidence."""

import runpy
from pathlib import Path


def test_fault_jobs_have_real_history_and_distinct_heldout_dates() -> None:
    script = runpy.run_path(str(Path("scripts/create_v10_plan.py")))
    seeds = set()
    for i, date in enumerate(script["TEST_DATES"]):
        for code in script["FAULTS"]:
            job = script["fault_job"](code, date, i)
            scene = job.scenario.background
            lab = scene.simulation.protocol
            first_disruption = min(
                [job.scenario.fault.start_s, *[m.start_s for m in scene.expanded_maintenance()]]
            )
            assert first_disruption - lab.warmup_s >= 60
            assert len(lab.stations) == 3 and job.max_attempts == 2
            assert scene.simulation.weather.source_start.date().isoformat() == date
            seeds.add(lab.seed)
    assert len(seeds) == 18


def test_normal_day_is_independent_half_hour_segments() -> None:
    script = runpy.run_path(str(Path("scripts/create_v10_plan.py")))
    jobs = [script["normal_job"](i) for i in range(48)]
    assert (
        sum(
            j.scenario.simulation.protocol.duration_s - j.scenario.simulation.protocol.warmup_s
            for j in jobs
        )
        == 86400
    )
    assert len({j.task_id for j in jobs}) == 48
    assert {j.scenario.simulation.weather.source_start.date().isoformat() for j in jobs} == {
        "2024-06-12"
    }
    assert {j.scenario.normal_scene for j in jobs} == {"N01", "N02", "N03", "N07", "N09", "N10"}
