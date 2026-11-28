"""Authorized maintenance plans; no fault targets or injection information."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from pvcomm.config.lab import Identifier, V01Config, V01Contract


class MaintenanceWindow(V01Contract):
    maintenance_id: Identifier
    scope: Literal["stations", "region"]
    station_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=120)]
    region_id: Identifier | None = None
    start_s: Annotated[int, Field(strict=True, ge=1)]
    duration_s: Annotated[int, Field(strict=True, ge=5, le=3600)]


@dataclass(frozen=True)
class StationMaintenance:
    maintenance_id: str
    station_id: str
    start_s: int
    duration_s: int
    planned: bool = True

    @property
    def ready_file(self) -> str:
        return f"master.maintenance-{self.maintenance_id}-{self.station_id}.closed"


def legacy_maintenance(config: V01Config) -> tuple[StationMaintenance, ...]:
    event = config.interruption
    if event is None:
        return ()
    return (
        StationMaintenance(
            "legacy", event.station_id, event.start_s, event.duration_s, event.planned
        ),
    )
