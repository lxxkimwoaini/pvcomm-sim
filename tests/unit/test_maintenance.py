"""Synthetic maintenance records only; real multi-station checks run in the lab."""

import copy
from typing import Any

import pytest

from pvcomm.config.fault_scenes import terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, RegionScope, StationScope
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.scenes import ScenarioConfig, canonical_hash
from pvcomm.qc.maintenance import audit_maintenance
from pvcomm.qc.maintenance_acceptance import recovery_outcomes


def template() -> FaultScenarioConfig:
    return terminal_scenario("A08", RegionScope(kind="region", region_id="R1"), 3)


def test_legacy_shape_and_hash_are_unchanged() -> None:
    # Snapshot from clean 3ef4f83; .1 omits the new field, including nested serializers.
    scene = normal_scenario("N10")
    assert "maintenance_windows" not in scene.model_dump(mode="json")
    assert "maintenance_windows" not in scene.model_dump_json()
    assert (
        canonical_hash(scene.model_dump(mode="json"))
        == "0fdc18d09680d9667ff5d3b82a0095e49d9be511b92a18ec02d48396753fda15"
    )
    fault = terminal_scenario("A08", StationScope(kind="station", station_ids=("PV01",)), 3)
    assert "maintenance_windows" not in fault.model_dump(mode="json")["background"]
    assert len(scene.expanded_maintenance()) == 1


def test_region_membership_and_partial_recovery_are_independent() -> None:
    fault = template()
    assert fault.target_stations() == ("PV01", "PV02")
    assert {e.station_id for e in fault.background.expanded_maintenance()} == {"PV01", "PV02"}
    raw = fault.model_dump(mode="json")
    raw["fault"]["scope"] = dict(kind="station", station_ids=["PV01"])
    mixed = FaultScenarioConfig.model_validate(raw)
    assert mixed.target_stations() == ("PV01",)
    assert len(mixed.background.expanded_maintenance()) == 2
    assert FaultScenarioConfig.model_validate_json(mixed.model_dump_json()) == mixed


@pytest.mark.parametrize(
    "change",
    [
        "old_version",
        "unknown",
        "duplicate",
        "region",
        "repeated",
        "overlap",
        "end",
        "fault_end",
        "unmaintained",
    ],
)
def test_invalid_maintenance_is_rejected(change: str) -> None:
    raw = template().model_dump(mode="json")
    scene = raw["background"]
    window = scene["maintenance_windows"][0]
    if change == "old_version":
        scene["schema_version"] = "0.4-scenario.1"
    elif change == "unknown":
        window["station_ids"] = ["PV99"]
    elif change == "duplicate":
        window["station_ids"] = ["PV01", "PV01"]
    elif change == "region":
        window["station_ids"] = ["PV01"]
    elif change == "repeated":
        scene["maintenance_windows"].append({**window, "maintenance_id": "maintenance_2"})
    elif change == "overlap":
        scene["business_schedule"].append(
            dict(kind="interrogation", schedule_id="bad", at_s=55, station_ids=["PV01"])
        )
    elif change == "end":
        window["duration_s"] = 3600
    elif change == "fault_end":
        raw["fault"]["start_s"] = 61
    else:
        raw["fault"]["scope"] = dict(kind="station", station_ids=["PV03"])
    with pytest.raises(ValueError):
        FaultScenarioConfig.model_validate(raw)


def evidence() -> tuple[Any, ...]:
    scene = template().background
    master: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    for index, station in enumerate(("PV01", "PV02")):

        def row(kind: str, stamp: float, _station: str = station, **fields: Any) -> dict[str, Any]:
            return dict(
                station_id=_station,
                event_id=f"{_station}_{kind}_{stamp}",
                event_type=kind,
                available_at=int(stamp * 1e9),
                ts_event_utc_ns=int(stamp * 1e9),
                **fields,
            )

        master.extend(
            [
                row(
                    "master_maintenance_plan",
                    1,
                    window_start_utc_ns=40 * 10**9,
                    window_end_utc_ns=60 * 10**9,
                    recovery_deadline_s=15,
                ),
                row("master_maintenance_connection_closed", 40.1),
                row("master_connection_state", 61, state="OPEN"),
            ]
        )
        recovered = 110 if station == "PV01" else 62
        for ioa in (1001, 1006):
            master.append(
                row(
                    "master_observed_point",
                    recovered + ioa / 10000,
                    ioa=ioa,
                    value=1,
                    quality_valid=True,
                )
            )
        terminal.extend([row("terminal_suspended", 40.2), row("terminal_resumed", 60.2)])
        for cot in (7, 10):
            objects.append(
                dict(
                    src_ip=scene.simulation.protocol.stations[index].address,
                    type_id=100,
                    cot=cot,
                    available_at=62 * 10**9,
                    apdu_id=f"{station}_{cot}",
                )
            )
        states.extend(row("state", at, source="pvlib_pvwatts_inverter_v02") for at in range(40, 60))
    return scene, {"scene_clock": {"start_utc_ns": 0}}, master, terminal, objects, states


def test_connection_protocol_and_business_recovery_remain_separate() -> None:
    report = audit_maintenance(*evidence())
    assert report["passed"]
    failed = report["stations"]["PV01"]
    normal = report["stations"]["PV02"]
    assert (
        failed["connection_recovered_utc_ns"]
        < failed["protocol_recovered_utc_ns"]
        < failed["business_recovered_utc_ns"]
    )
    assert not failed["business_recovered_within_deadline"]
    assert normal["business_recovered_within_deadline"]


@pytest.mark.parametrize(
    "failure", ["late_plan", "wrong_station", "no_suspend", "no_gi_end", "physics_gap"]
)
def test_independent_maintenance_audit_negative_cases(failure: str) -> None:
    args = copy.deepcopy(evidence())
    if failure == "late_plan":
        args[2][0]["available_at"] = 41 * 10**9
    elif failure == "wrong_station":
        args[2][0]["station_id"] = "PV03"
    elif failure == "no_suspend":
        args[3].pop(0)
    elif failure == "no_gi_end":
        args[4].pop(1)
    else:
        args[5].pop(5)
    assert not audit_maintenance(*args)["passed"]


def test_station_group_has_no_region_and_preserves_task_exemption_boundary() -> None:
    raw = template().background.model_dump(mode="json")
    raw["maintenance_windows"][0].update(scope="stations", region_id=None)
    assert ScenarioConfig.model_validate(raw)
    raw["maintenance_windows"][0]["region_id"] = "R1"
    with pytest.raises(ValueError, match="cannot specify a region"):
        ScenarioConfig.model_validate(raw)


def test_grouped_acceptance_requires_target_recovery_but_not_a_fault_on_normal_station() -> None:
    raw = template().model_dump(mode="json")
    raw["fault"]["scope"] = dict(kind="station", station_ids=["PV01"])
    config = FaultScenarioConfig.model_validate(raw)
    maintenance = audit_maintenance(*evidence())
    labels = [
        dict(
            station_id=s,
            label_status="valid",
            service_violation=s == "PV01",
            fault_applied=s == "PV01",
            recovery_censored=s != "PV01",
        )
        for s in ("PV01", "PV02", "PV03")
    ]
    assessments = {"PV03": [dict(outcome="accepted_complete")]}
    assert recovery_outcomes(config, maintenance, labels, assessments)
    labels[0]["recovery_censored"] = True
    assert not recovery_outcomes(config, maintenance, labels, assessments)
    labels[0]["recovery_censored"] = False
    labels[1]["service_violation"] = None
    assert not recovery_outcomes(config, maintenance, labels, assessments)
