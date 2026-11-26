"""Normal dispatch allocation using configured capacity or fresh M2 telemetry."""

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from pvcomm.config.lab import V01StationConfig
from pvcomm.config.multisite import V03Config
from pvcomm.domain.command import CommandResult, ObservationUnavailable
from pvcomm.protocol.c104_runtime import RecordEvent


class DispatchMaster(Protocol):
    def available_power(self, station_config: V01StationConfig, /) -> float: ...

    def send_setpoint(
        self,
        station_config: V01StationConfig,
        /,
        target_kw: float,
        action: str,
        *,
        dispatch_id: str | None = None,
        raise_on_failure: bool = True,
    ) -> CommandResult: ...


def allocate(
    total_kw: float, capacities: dict[str, float], available: dict[str, float] | None = None
) -> tuple[dict[str, float], float]:
    if not math.isfinite(total_kw) or total_kw < 0 or not capacities:
        raise ValueError("invalid dispatch request")
    if any(not math.isfinite(v) or v <= 0 for v in capacities.values()):
        raise ValueError("invalid station capacity")
    if available is not None and set(available) != set(capacities):
        raise ValueError("allocation requires all selected fresh observations")
    if available is not None and any(not math.isfinite(v) or v < 0 for v in available.values()):
        raise ValueError("invalid observed availability")
    weights = {
        s: c if available is None else min(c, max(0.0, available[s])) for s, c in capacities.items()
    }
    if any(not math.isfinite(v) for v in weights.values()):
        raise ValueError("invalid observed availability")
    achievable = min(total_kw, sum(weights.values()))
    denominator = sum(weights.values())
    return (
        {s: achievable * w / denominator if denominator else 0.0 for s, w in weights.items()},
        total_kw - achievable,
    )


def execute_dispatches(
    config: V03Config,
    master: DispatchMaster,
    wait_until: Callable[[float], None],
    record: RecordEvent,
) -> None:
    for task in config.dispatch_tasks:
        wait_until(task.at_s)
        stations = [s for s in config.protocol.stations if s.region_id in task.region_ids]
        requested_count = len(stations)
        excluded: dict[str, str] = {}
        availability = None
        if task.allocation == "observed_available":
            availability = {}
            for station in stations:
                try:
                    availability[station.station_id] = master.available_power(station)
                except ObservationUnavailable as exc:
                    excluded[station.station_id] = exc.reason
            stations = [s for s in stations if s.station_id not in excluded]
        targets, unallocated = (
            allocate(task.total_kw, {s.station_id: s.capacity_kw for s in stations}, availability)
            if stations
            else ({}, task.total_kw)
        )
        record(
            "master_dispatch_allocated",
            {
                "dispatch_id": task.dispatch_id,
                "region_ids": list(task.region_ids),
                "requested_kw": task.total_kw,
                "allocated_kw": sum(targets.values()),
                "unallocated_kw": unallocated,
                "allocation": task.allocation,
                "targets_kw": targets,
                "requested_station_count": requested_count,
                "eligible_station_count": len(stations),
                "excluded_observations": excluded,
                "degradation_policy": "exclude_unavailable_observations_continue_eligible",
            },
        )
        with ThreadPoolExecutor(max_workers=task.batch_size) as pool:
            for offset in range(0, len(stations), task.batch_size):
                wait_until(task.at_s + offset // task.batch_size * (3 + task.batch_interval_s))
                batch = stations[offset : offset + task.batch_size]
                futures = {
                    s.station_id: pool.submit(
                        master.send_setpoint,
                        s,
                        targets[s.station_id],
                        "dispatch",
                        dispatch_id=task.dispatch_id,
                        raise_on_failure=False,
                    )
                    for s in batch
                }
                for station_id, future in futures.items():
                    result = future.result()
                    record(
                        "master_dispatch_station_result",
                        {
                            "dispatch_id": task.dispatch_id,
                            "station_id": station_id,
                            "task_id": result.task_id,
                            "outcome": result.outcome,
                            "positive_confirmation": result.positive_confirmation,
                        },
                    )
        record("master_dispatch_transmissions_finished", {"dispatch_id": task.dispatch_id})
