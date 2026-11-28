"""Versioned counterfactual pairs with an explicit, narrow difference budget."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from pvcomm.config.fault_scenes import a01_scenario, terminal_scenario
from pvcomm.config.faults import FaultScenarioConfig, StationScope
from pvcomm.config.lab import Identifier, V01Contract
from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.scenes import ScenarioConfig, canonical_hash


class PairConfig(V01Contract):
    schema_version: Literal["0.4-pair.1"] = "0.4-pair.1"
    pair_type: Literal["P03", "P04", "P06", "P03_recovery"]
    pair_id: Identifier
    normal: ScenarioConfig
    changed: FaultScenarioConfig

    def shared_conditions(self, scene: ScenarioConfig) -> dict[str, object]:
        raw = scene.model_dump(mode="json")
        # These identities do not describe physical initial conditions.
        raw.pop("normal_scene")
        protocol = raw["simulation"]["protocol"]
        protocol.pop("run_id")
        if self.pair_type == "P03":
            # P03 changes known authorization, as required by V1.3 搂9.
            protocol.pop("interruption")
        return raw

    @model_validator(mode="after")
    def validate_members(self) -> Self:
        normal, changed = self.normal, self.changed
        if normal.pair_id != self.pair_id or changed.background.pair_id != self.pair_id:
            raise ValueError("both members must use the declared pair identity")
        if normal.source_group_id != changed.background.source_group_id:
            raise ValueError("pair members must share the original source group")
        if self.shared_conditions(normal) != self.shared_conditions(changed.background):
            raise ValueError("pair physical/weather/network/seed/schedule conditions differ")
        expected = {"P03": "A01", "P04": "A06", "P06": "A05", "P03_recovery": "A08"}
        if changed.fault.fault_type != expected[self.pair_type]:
            raise ValueError("pair mechanism does not match V1.3")
        maintenance = normal.simulation.protocol.interruption
        if self.pair_type == "P03" and (
            maintenance is None
            or not maintenance.planned
            or changed.background.simulation.protocol.interruption is not None
            or changed.target_stations() != (maintenance.station_id,)
            or changed.fault.start_s != maintenance.start_s
            or changed.fault.duration_s != maintenance.duration_s
        ):
            raise ValueError("P03 must change authorization for the same interruption window")
        return self

    def shared_sha256(self) -> str:
        return canonical_hash(self.shared_conditions(self.normal))


def development_pair(code: str, pair_id: str, variant: int = 0) -> PairConfig:
    """Three-site functional cases; never formal dataset generation parameters."""
    scope = StationScope(kind="station", station_ids=("PV01",))
    if code == "P03":
        changed = a01_scenario(scope, 20, variant).model_dump(mode="json")
        changed["fault"]["start_s"] = 40
        background = changed["background"]
        background["simulation"]["protocol"].update(duration_s=170, release_after_s=25)
        background["business_schedule"] = [
            dict(kind="interrogation", schedule_id="recovery", at_s=100, station_ids=["PV01"])
        ]
        normal = ScenarioConfig.model_validate(background).model_dump(mode="json")
        normal["normal_scene"] = "N10"
        normal["simulation"]["protocol"]["interruption"] = dict(
            station_id="PV01", start_s=40, duration_s=20, planned=True
        )
    elif code in ("P04", "P06"):
        changed = terminal_scenario(
            "A06" if code == "P04" else "A05", scope, 30 if code == "P04" else 15, variant
        ).model_dump(mode="json")
        base = normal_scenario("N07" if code == "P04" else "N09", variant)
        background = changed["background"]
        background["normal_scene"] = base.normal_scene
        if code == "P04":
            background["simulation"]["weather"] = base.simulation.weather.model_dump(mode="json")
            background["simulation"]["spatial_cloud"] = None
            background["source_group_id"] = base.source_group_id
            background["business_schedule"][0]["targets_kw"]["PV01"] = 25.0 + variant
        else:
            background["simulation"]["device_operations"] = [
                dict(station_id="PV01", at_s=10, enabled=False),
                # Both members retain the same device operation schedule.
                dict(station_id="PV01", at_s=40, enabled=True),
            ]
            background["business_schedule"][0]["at_s"] = 27
        normal = ScenarioConfig.model_validate(background).model_dump(mode="json")
    elif code == "P03_recovery":
        changed = terminal_scenario("A08", scope, 3, variant).model_dump(mode="json")
        normal = changed["background"]
    else:
        raise ValueError("unknown V0.4 development pair")
    # Copy through model validation; never alias the two mutable raw documents.
    normal = ScenarioConfig.model_validate(normal).model_dump(mode="json")
    normal["pair_id"] = pair_id
    changed["background"]["pair_id"] = pair_id
    return PairConfig.model_validate(
        dict(pair_type=code, pair_id=pair_id, normal=normal, changed=changed)
    )
