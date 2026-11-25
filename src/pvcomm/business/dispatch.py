"""Normal dispatch allocation using configured capacity or fresh M2 telemetry."""

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pvcomm.config.multisite import V03Config
from pvcomm.protocol.c104_runtime import C104Master, RecordEvent


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
    config: V03Config, master: C104Master, wait_until: Callable[[float], None], record: RecordEvent
) -> None:
    for task in config.dispatch_tasks:
        wait_until(task.at_s)
        stations = [s for s in config.protocol.stations if s.region_id in task.region_ids]
        availability = (
            {s.station_id: master.available_power(s) for s in stations}
            if task.allocation == "observed_available"
            else None
        )
        targets, unallocated = allocate(
            task.total_kw, {s.station_id: s.capacity_kw for s in stations}, availability
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
                    )
                    for s in batch
                }
                for station_id, future in futures.items():
                    try:
                        future.result()
                    except RuntimeError as exc:
                        record(
                            "master_dispatch_station_failed",
                            {
                                "dispatch_id": task.dispatch_id,
                                "station_id": station_id,
                                "error": str(exc),
                            },
                        )
        record("master_dispatch_transmissions_finished", {"dispatch_id": task.dispatch_id})
