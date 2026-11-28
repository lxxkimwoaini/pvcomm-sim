"""Coverage recipes are configuration tests, never real experiment evidence."""

from collections import Counter

import pytest

from pvcomm.config.batch import FaultJob
from pvcomm.config.coverage import STRENGTHS, Stage, fault_coverage_matrix
from pvcomm.config.fault_scenes import a01_scenario, a02_scenario, terminal_scenario
from pvcomm.config.faults import GlobalScope, RegionScope, StationScope
from pvcomm.config.scenes import canonical_hash


def test_legacy_fault_templates_preserve_frozen_three_site_hashes() -> None:
    # Aggregated from the unmodified 8519edb source, including PV03 as a target.
    rows: list[object] = []
    for scope in (
        StationScope(kind="station", station_ids=("PV01",)),
        StationScope(kind="station", station_ids=("PV03",)),
        RegionScope(kind="region", region_id="R1"),
        GlobalScope(kind="global"),
    ):
        for variant in range(3):
            scenes = [
                a01_scenario(scope, 20, variant),
                a02_scenario(scope, 1000, "master_to_rtu", variant),
            ]
            scenes.extend(
                terminal_scenario(code, scope, strength, variant)
                for code, strength in (("A04", 5), ("A05", 5), ("A06", 30), ("A08", 3))
            )
            rows.extend(scene.model_dump(mode="json") for scene in scenes)
    assert len(rows) == 72
    assert (
        canonical_hash(rows) == "3156ce714db736dc5a189fd7db64e6d189da7a834f2f3cf4d904990c0c700220"
    )


def test_predeclared_coverage_is_independent_and_stratified() -> None:
    names: tuple[Stage, ...] = ("scale-10", "scale-30", "coverage")
    stages = [fault_coverage_matrix(stage) for stage in names]
    assert [len(s.tasks) for s in stages] == [6, 6, 228]
    tasks = [t for stage in stages for t in stage.tasks if isinstance(t, FaultJob)]
    assert len(tasks) == 240
    assert len({t.task_id for t in tasks}) == 240
    assert len({t.scenario.background.simulation.protocol.seed for t in tasks}) == 240
    assert Counter(t.scenario.fault.fault_type for t in tasks) == dict.fromkeys(STRENGTHS, 40)
    for code in STRENGTHS:
        own = [t.scenario for t in tasks if t.scenario.fault.fault_type == code]
        assert Counter(len(s.background.simulation.protocol.stations) for s in own) == {
            3: 4,
            10: 12,
            30: 24,
        }
        assert {len(s.target_stations()) == 1 for s in own} == {True, False}
        assert {s.fault.scope.kind for s in own} == {"station", "region"}
        assert len({s.background.simulation.weather.source_start for s in own}) == 3
    for stage, size in zip(stages[:2], (10, 30), strict=True):
        own_tasks = [t for t in stage.tasks if isinstance(t, FaultJob)]
        assert all(
            len(t.scenario.background.simulation.protocol.stations) == size for t in own_tasks
        )
        assert all(t.scenario.fault.scope.kind == "region" for t in own_tasks)
    assert stages[0] == fault_coverage_matrix("scale-10")
    with pytest.raises(ValueError, match="unknown coverage stage"):
        fault_coverage_matrix("unknown")  # type: ignore[arg-type]


@pytest.mark.parametrize("count", (10, 30))
def test_expanded_faults_keep_region_members_and_non_target_business(count: int) -> None:
    scope = RegionScope(kind="region", region_id="R1")
    scenes = [
        a01_scenario(scope, 20, site_count=count),
        a02_scenario(scope, 1000, "rtu_to_master", site_count=count),
    ]
    scenes.extend(
        terminal_scenario(code, scope, STRENGTHS[code][1], site_count=count)
        for code in ("A04", "A05", "A06", "A08")
    )
    for scene in scenes:
        members = {
            s.station_id
            for s in scene.background.simulation.protocol.stations
            if s.region_id == "R1"
        }
        assert set(scene.target_stations()) == members
        assert len(members) == (count + 2) // 3
        assert len(scene.background.simulation.regions) == 3
        controls = [
            t
            for t in scene.background.business_schedule
            if t.schedule_id in ("background", "task_1") and set(t.station_ids).isdisjoint(members)
        ]
        assert len(controls) == 1
    with pytest.raises(ValueError, match="unknown or duplicate fault station"):
        a01_scenario(StationScope(kind="station", station_ids=("PV99",)), 20, site_count=count)
