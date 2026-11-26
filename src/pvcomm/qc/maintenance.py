"""Independent authorized maintenance evidence, separate from service labels."""

from typing import Any

from pvcomm.config.scenes import ScenarioConfig


def audit_maintenance(
    scenario: ScenarioConfig,
    manifest: dict[str, Any],
    master: list[dict[str, Any]],
    terminal: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    start = manifest["scene_clock"]["start_utc_ns"]
    result = {}
    stations = {s.station_id: s for s in scenario.simulation.protocol.stations}
    for event in scenario.expanded_maintenance():
        if not event.planned:
            continue
        begin = start + event.start_s * 10**9
        end = begin + event.duration_s * 10**9
        deadline = end + 15 * 10**9
        own = [r for r in master if r.get("station_id") == event.station_id]
        plans = [r for r in own if r["event_type"] == "master_maintenance_plan"]
        suspended = [
            r
            for r in terminal
            if r.get("station_id") == event.station_id and r["event_type"] == "terminal_suspended"
        ]
        resumed = [
            r
            for r in terminal
            if r.get("station_id") == event.station_id and r["event_type"] == "terminal_resumed"
        ]
        closed = [r for r in own if r["event_type"] == "master_maintenance_connection_closed"]
        opened = [
            r
            for r in own
            if r["event_type"] == "master_connection_state"
            and r["state"] == "OPEN"
            and r["available_at"] >= end
        ]
        valid_plan = bool(
            len(plans) == 1
            and plans[0]["available_at"] < begin
            and plans[0]["window_start_utc_ns"] == begin
            and plans[0]["window_end_utc_ns"] == end
            and plans[0]["recovery_deadline_s"] == 15
        )
        lifecycle = bool(
            len(suspended) == len(resumed) == len(closed) == 1
            and opened
            and begin <= closed[0]["available_at"] <= suspended[0]["ts_event_utc_ns"] < end
            and resumed[0]["ts_event_utc_ns"] >= end
        )
        site = stations[event.station_id]
        # Wire GI confirms a live protocol session; measurements prove business recovery.
        gi = [
            r
            for r in objects
            if r["src_ip"] == site.address
            and r["type_id"] == 100
            and r["cot"] in (7, 10)
            and not r.get("negative_confirm")
            and not r.get("test_flag")
            and r["available_at"] >= end
        ]
        observations = {}
        for ioa in (1001, 1006):
            seen = [
                r
                for r in own
                if r["event_type"] == "master_observed_point"
                and r.get("ioa") == ioa
                and r.get("quality_valid") is not False
                and r.get("value") is not None
                and r["available_at"] >= end
            ]
            observations[ioa] = min(seen, key=lambda r: r["available_at"]) if seen else None
        business_at = (
            max(r["available_at"] for r in observations.values() if r)
            if all(observations.values())
            else None
        )
        times = sorted(
            r["ts_event_utc_ns"]
            for r in states
            if r.get("station_id") == event.station_id
            and r.get("source") == "pvlib_pvwatts_inverter_v02"
            and begin <= r["ts_event_utc_ns"] < end
        )
        gaps = [(b - a) / 1e9 for a, b in zip(times, times[1:], strict=False)]
        physics = bool(
            gaps and times[0] - begin <= 1.5e9 and end - times[-1] <= 1.5e9 and max(gaps) <= 1.5
        )
        protocol_cots = {
            cot: min(r["available_at"] for r in gi if r["cot"] == cot)
            for cot in (7, 10)
            if any(r["cot"] == cot for r in gi)
        }
        result[event.station_id] = dict(
            passed=valid_plan
            and lifecycle
            and physics
            and {7, 10}.issubset({r["cot"] for r in gi}),
            authorized_plan_valid=valid_plan,
            lifecycle_valid=lifecycle,
            maintenance_id=event.maintenance_id,
            planned_interval_utc_ns=[begin, end],
            recovery_deadline_utc_ns=deadline,
            connection_recovered_utc_ns=opened[0]["available_at"] if opened else None,
            protocol_recovered_utc_ns=max(protocol_cots.values())
            if len(protocol_cots) == 2
            else None,
            business_recovered_utc_ns=business_at,
            business_recovered_within_deadline=business_at is not None and business_at <= deadline,
            physical_continuity=dict(
                passed=physics, samples=len(times), maximum_gap_s=max(gaps, default=None)
            ),
            plan_event_refs=[r["event_id"] for r in plans],
            terminal_event_refs=[r["event_id"] for r in [*suspended, *resumed]],
            connection_event_refs=[r["event_id"] for r in [*closed, *opened]],
            protocol_apdu_refs=[r["apdu_id"] for r in gi],
            business_event_refs=[r["event_id"] for r in observations.values() if r],
        )
    return dict(
        passed=all(r["passed"] for r in result.values()),
        stations=result,
        scope="authorized_plan_and_recovery_evidence_not_service_labels",
    )
