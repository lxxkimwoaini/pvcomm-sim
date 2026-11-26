"""Small A01 development configurations, not formal dataset parameters."""

from typing import Any

from pvcomm.config.faults import FaultScenarioConfig, FaultScope
from pvcomm.config.normal_scenes import normal_scenario


def a01_scenario(scope: FaultScope, duration_s: int, variant: int = 0) -> FaultScenarioConfig:
    background = normal_scenario("N03", variant).model_dump(mode="json")
    simulation = background["simulation"]
    simulation["protocol"]["duration_s"] = 180 if duration_s == 60 else 150
    simulation["regions"] = simulation["regions"][:2]
    for station, region, address in zip(
        simulation["protocol"]["stations"],
        ("R1", "R1", "R2"),
        ("10.204.1.2", "10.204.1.3", "10.204.2.2"),
        strict=True,
    ):
        station.update(region_id=region, address=address)
    raw: dict[str, Any] = {
        "schema_version": "0.4-fault.1",
        "purpose": "single_fault_development",
        "background": background,
        "fault": {
            "event_id": "event_1",
            "fault_type": "A01",
            "start_s": 20,
            "duration_s": duration_s,
            "scope": scope.model_dump(mode="json"),
        },
    }
    config = FaultScenarioConfig.model_validate(raw)
    targets = config.target_stations()
    tasks = []
    if "PV03" not in targets:
        tasks.append(
            {
                "kind": "setpoint",
                "schedule_id": "task_1",
                "at_s": 22,
                "station_ids": ["PV03"],
                "targets_kw": {"PV03": 35.0},
            }
        )
    tasks.append(
        {
            "kind": "interrogation",
            "schedule_id": "task_2",
            "at_s": 20 + duration_s + 20,
            "station_ids": list(targets),
        }
    )
    raw["background"]["business_schedule"] = tasks
    return FaultScenarioConfig.model_validate(raw)
