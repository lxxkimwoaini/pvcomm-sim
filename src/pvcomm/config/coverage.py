"""Predeclared P0 acceptance trials; no detector outputs or outcome-based sampling."""

from typing import Literal

from pvcomm.config.batch import BatchConfig, FaultJob
from pvcomm.config.fault_scenes import a01_scenario, a02_scenario, terminal_scenario
from pvcomm.config.faults import FaultScope, RegionScope, StationScope

Stage = Literal["scale-10", "scale-30", "coverage"]
STRENGTHS = {
    "A01": (5, 20, 60),
    "A02": (200, 1000, 3000),
    "A04": (2, 5, 12),
    "A05": (2, 5, 15),
    "A06": (10, 30, 60),
    "A08": (1, 3, 5),
}


def fault_coverage_matrix(stage: Stage) -> BatchConfig:
    """Six trials per scale gate, then 228 trials: exactly 40 per class total.

    Existing retained development evidence is additional, never duplicated here.
    All trials keep actual realtime and the previously accepted short-case windows.
    """
    if stage not in ("scale-10", "scale-30", "coverage"):
        raise ValueError("unknown coverage stage")
    trials = (0,) if stage == "scale-10" else (1,) if stage == "scale-30" else range(2, 40)
    jobs = []
    for trial in trials:
        count = 10 if trial == 0 or 6 <= trial < 17 else 3 if 2 <= trial < 6 else 30
        for class_index, (code, strengths) in enumerate(STRENGTHS.items()):
            # Scale gates exercise real regional links and every terminal in R1.
            region = 1 + trial // 9 % (2 if count == 3 else 3)
            scope: FaultScope = RegionScope(kind="region", region_id=f"R{region}")
            if trial >= 2 and trial % 3 != 2:
                first = (trial * 7 + class_index) % count
                indices = (first,) if trial % 3 == 0 else (first, (first + count // 2) % count)
                scope = StationScope(
                    kind="station", station_ids=tuple(f"PV{i + 1:02d}" for i in indices)
                )
            strength = strengths[trial % 3]
            variant = trial % 3
            if code == "A01":
                scene = a01_scenario(scope, strength, variant, site_count=count)
            elif code == "A02":
                scene = a02_scenario(
                    scope,
                    strength,
                    ("master_to_rtu", "rtu_to_master")[trial % 2],
                    variant,
                    site_count=count,
                )
            else:
                scene = terminal_scenario(code, scope, strength, variant, site_count=count)
            raw = scene.model_dump(mode="json")
            simulation = raw["background"]["simulation"]
            seed = 800_000 + class_index * 1000 + trial
            simulation["protocol"]["seed"] = seed
            for station in simulation["protocol"]["stations"]:
                station["access_link"]["seed"] += seed
            for area in simulation["regions"]:
                area["uplink"]["seed"] += seed
            jobs.append(
                FaultJob.model_validate(
                    dict(
                        kind="fault",
                        task_id=f"trial_{code}_{trial:02d}",
                        max_attempts=1,
                        scenario=raw,
                    )
                )
            )
    return BatchConfig(matrix_id=f"p0_{stage.replace('-', '_')}_v1", tasks=tuple(jobs))
