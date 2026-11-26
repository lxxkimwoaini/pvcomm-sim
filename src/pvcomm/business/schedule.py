"""Execute due business intentions through the existing protocol adapter only."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from pvcomm.business.dispatch import DispatchMaster, execute_dispatch
from pvcomm.config.lab import V01StationConfig
from pvcomm.config.multisite import DispatchTask
from pvcomm.config.scenes import AllocationTask, BusinessTask, ScenarioConfig, SetpointTask
from pvcomm.protocol.c104_runtime import RecordEvent


class ScheduleMaster(DispatchMaster, Protocol):
    def interrogate(
        self, station_config: V01StationConfig, *, dispatch_id: str, raise_on_failure: bool = True
    ) -> bool: ...


def execute_schedule(
    scenario: ScenarioConfig,
    master: ScheduleMaster,
    wait_until: Callable[[float], None],
    record: RecordEvent,
) -> None:
    stations = {s.station_id: s for s in scenario.simulation.protocol.stations}

    def execute(task: BusinessTask) -> None:
        # Published at actual issue time, never the entire future scenario.
        record(
            "master_schedule_started",
            {
                "dispatch_id": task.schedule_id,
                "kind": task.kind,
                "station_ids": list(task.station_ids),
                "scheduled_at_s": task.at_s,
            },
        )
        members: list[V01StationConfig] = [stations[s] for s in task.station_ids]
        if isinstance(task, AllocationTask):
            dispatch = DispatchTask(
                dispatch_id=task.schedule_id,
                at_s=task.at_s,
                region_ids=tuple(dict.fromkeys(s.region_id for s in members)),
                total_kw=task.total_kw,
                allocation=task.allocation,
                batch_size=task.batch_size,
                batch_interval_s=task.batch_interval_s,
            )
            execute_dispatch(dispatch, members, master, wait_until, record)
        else:
            with ThreadPoolExecutor(max_workers=len(members)) as pool:

                def issue(station: V01StationConfig) -> None:
                    if isinstance(task, SetpointTask):
                        result = master.send_setpoint(
                            station,
                            task.targets_kw[station.station_id],
                            "scheduled",
                            dispatch_id=task.schedule_id,
                            raise_on_failure=False,
                        )
                        record(
                            "master_dispatch_station_result",
                            {
                                "dispatch_id": task.schedule_id,
                                "station_id": station.station_id,
                                "task_id": result.task_id,
                                "outcome": result.outcome,
                                "positive_confirmation": result.positive_confirmation,
                            },
                        )
                    else:
                        master.interrogate(
                            station, dispatch_id=task.schedule_id, raise_on_failure=False
                        )

                list(pool.map(issue, members))
        record("master_schedule_finished", {"dispatch_id": task.schedule_id})

    # An empty plan submits no work. Disjoint station tasks can overlap in time.
    with ThreadPoolExecutor(max_workers=len(stations)) as pool:
        futures = []
        for task in scenario.expanded_schedule():
            wait_until(task.at_s)
            futures.append(pool.submit(execute, task))
        for future in futures:
            future.result()
