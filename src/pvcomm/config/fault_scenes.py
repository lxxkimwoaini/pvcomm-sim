"""Bounded development configurations, not formal dataset parameters."""

from typing import Any

from pvcomm.config.faults import FaultScenarioConfig, FaultScope
from pvcomm.config.normal_scenes import normal_scenario


def control_station(config: FaultScenarioConfig) -> str | None:
    """Use an actual non-target endpoint; retain the legacy three-site choice."""
    if len(config.background.simulation.protocol.stations) == 3:
        return "PV03" if "PV03" not in config.target_stations() else None
    return next(
        (
            s.station_id
            for s in reversed(config.background.simulation.protocol.stations)
            if s.station_id not in config.target_stations()
        ),
        None,
    )


def a01_scenario(
    scope: FaultScope, duration_s: int, variant: int = 0, *, site_count: int = 3
) -> FaultScenarioConfig:
    background = normal_scenario("N03", variant, site_count=site_count).model_dump(mode="json")
    simulation = background["simulation"]
    simulation["protocol"]["duration_s"] = 180 if duration_s == 60 else 150
    if site_count == 3:
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
    control = control_station(config)
    if control:
        tasks.append(
            {
                "kind": "setpoint",
                "schedule_id": "task_1",
                "at_s": 22,
                "station_ids": [control],
                "targets_kw": {control: 35.0},
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
    scope: FaultScope,
    additional_delay_ms: int,
    direction: str,
    variant: int = 0,
    *,
    site_count: int = 3,
) -> FaultScenarioConfig:
    """Bounded delay experiment; strengths are fixed by V1.3."""
    raw = a01_scenario(scope, 20, variant, site_count=site_count).model_dump(mode="json")
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
    control = control_station(config)
    if control:
        tasks.insert(
            0,
            dict(
                kind="setpoint",
                schedule_id="background",
                at_s=24,
                station_ids=[control],
                targets_kw={control: 35.0},
            ),
        )
    raw["background"]["business_schedule"] = tasks
    return FaultScenarioConfig.model_validate(raw)


def terminal_scenario(
    code: str, scope: FaultScope, strength: int, variant: int = 0, *, site_count: int = 3
) -> FaultScenarioConfig:
    """Implemented RTU mechanisms using the same background and runner."""
    base = a01_scenario(scope, 20, variant, site_count=site_count)
    raw = base.model_dump(mode="json")
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
    targets = base.target_stations()
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
        if len(targets) > 1:
            protocol["interruption"] = None
            raw["background"].update(
                schema_version="0.4-scenario.2",
                maintenance_windows=[
                    dict(
                        maintenance_id="maintenance_1",
                        scope="region" if scope.kind == "region" else "stations",
                        station_ids=list(targets),
                        region_id=scope.region_id if scope.kind == "region" else None,
                        start_s=40,
                        duration_s=20,
                    )
                ],
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
    if code == "A04":
        tasks.append(
            dict(
                kind="interrogation",
                schedule_id="during_hold",
                at_s=start + 1,
                station_ids=list(targets),
            )
        )
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
    control = control_station(base)
    if control:
        tasks.append(
            dict(
                kind="setpoint",
                schedule_id="background",
                at_s=26,
                station_ids=[control],
                targets_kw={control: 35.0},
            )
        )
    tasks.append(
        dict(kind="interrogation", schedule_id="recovery", at_s=recovery, station_ids=list(targets))
    )
    raw["background"]["business_schedule"] = tasks
    return FaultScenarioConfig.model_validate(raw)
