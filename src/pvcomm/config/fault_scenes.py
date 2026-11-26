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


def a02_scenario(
    scope: FaultScope, additional_delay_ms: int, direction: str, variant: int = 0
) -> FaultScenarioConfig:
    """Bounded three-site delay experiment; strengths are fixed by V1.3."""
    raw = a01_scenario(scope, 20, variant).model_dump(mode="json")
    raw["schema_version"] = "0.4-fault.2"
    raw["background"]["simulation"]["protocol"]["duration_s"] = 160
    raw["fault"] = dict(
        event_id="event_1",
        fault_type="A02",
        start_s=20,
        duration_s=30,
        additional_delay_ms=additional_delay_ms,
        direction=direction,
        scope=scope.model_dump(mode="json"),
    )
    config = FaultScenarioConfig.model_validate(raw)
    tasks = [
        dict(
            kind="setpoint",
            schedule_id="task_1",
            at_s=27,
            station_ids=list(config.target_stations()),
            targets_kw={s: 20.0 for s in config.target_stations()},
        ),
        dict(
            kind="interrogation",
            schedule_id="task_2",
            at_s=90,
            station_ids=list(config.target_stations()),
        ),
    ]
    if "PV03" not in config.target_stations():
        tasks.insert(
            0,
            dict(
                kind="setpoint",
                schedule_id="background",
                at_s=24,
                station_ids=["PV03"],
                targets_kw={"PV03": 35.0},
            ),
        )
    raw["background"]["business_schedule"] = tasks
    return FaultScenarioConfig.model_validate(raw)


def terminal_scenario(
    code: str, scope: FaultScope, strength: int, variant: int = 0
) -> FaultScenarioConfig:
    """Implemented RTU mechanisms using the same three-site background and runner."""
    raw = a01_scenario(scope, 20, variant).model_dump(mode="json")
    raw["schema_version"] = "0.4-fault.3"
    protocol = raw["background"]["simulation"]["protocol"]
    raw["background"]["business_schedule"] = []
    fields = {
        "A04": "missing_cycles",
        "A05": "response_delay_s",
        "A06": "feedback_duration_s",
        "A08": "recovery_cycles",
    }
    if code not in fields:
        raise ValueError("unknown terminal fault")
    targets = a01_scenario(scope, 20, variant).target_stations()
    duration = (
        max(s["telemetry_period_s"] for s in protocol["stations"] if s["station_id"] in targets)
        * strength
        if code == "A04"
        else strength
        if code == "A06"
        else 15 * strength
        if code == "A08"
        else 30
    )
    start = 60 if code == "A08" else 20
    # A06 hold begins at actual acceptance, rather than the earlier arm time.
    recovery = max(100, start + duration + 20)
    protocol["duration_s"] = recovery + 70
    if code == "A08":
        protocol.update(
            release_after_s=25,
            interruption=dict(station_id=targets[0], start_s=40, duration_s=20, planned=True),
        )
    raw["fault"] = dict(
        event_id="event_1",
        fault_type=code,
        start_s=start,
        duration_s=duration,
        scope=scope.model_dump(mode="json"),
        **{fields[code]: strength},
    )
    tasks: list[dict[str, Any]] = []
    if code in ("A05", "A06"):
        tasks.append(
            dict(
                kind="setpoint",
                schedule_id="task_1",
                at_s=25,
                station_ids=list(targets),
                targets_kw={s: 20.0 for s in targets},
            )
        )
    if "PV03" not in targets:
        tasks.append(
            dict(
                kind="setpoint",
                schedule_id="background",
                at_s=26,
                station_ids=["PV03"],
                targets_kw={"PV03": 35.0},
            )
        )
    tasks.append(
        dict(kind="interrogation", schedule_id="recovery", at_s=recovery, station_ids=list(targets))
    )
    raw["background"]["business_schedule"] = tasks
    return FaultScenarioConfig.model_validate(raw)
