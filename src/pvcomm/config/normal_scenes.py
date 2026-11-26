"""Small normal development cases, never frozen formal dataset parameters."""

from typing import Any

from pvcomm.config.presets import configuration
from pvcomm.config.scenes import ScenarioConfig

SCENE_CODES = tuple(f"N{i:02d}" for i in range(1, 11))


def normal_scenario(code: str, variant: int = 0) -> ScenarioConfig:
    if code not in SCENE_CODES or variant not in (0, 1, 2):
        raise ValueError("unknown normal scene or development variant")
    simulation = configuration(3, 180 if code == "N06" else 120)
    simulation.update(dispatch_tasks=[], repeat_command_after_s=None, spatial_cloud=None)
    simulation["weather"]["cloud_shadow"] = None
    simulation["weather"]["source_start"] = f"2024-06-{23 + variant:02d}T04:00:00Z"
    simulation["protocol"]["seed"] = 104 + variant
    simulation["protocol"]["run_id"] = "template_only"
    for region in simulation["regions"]:
        region["uplink"]["seed"] += variant * 100
    for site in simulation["protocol"]["stations"]:
        site["access_link"]["seed"] += variant * 100
    tasks: list[dict[str, Any]] = []

    def command(at_s: int, target_kw: float, identity: str = "task_1") -> dict[str, Any]:
        return dict(
            kind="setpoint",
            schedule_id=identity,
            at_s=at_s,
            jitter_s=3,
            station_ids=["PV01"],
            targets_kw={"PV01": target_kw},
        )

    if code == "N02":
        simulation["weather"]["source_start"] = (
            "2024-06-23T16:00:00Z",
            "2024-06-23T21:11:45Z",
            "2024-06-24T11:41:00Z",
        )[variant]
    elif code == "N03":
        simulation["weather"]["cloud_shadow"] = dict(
            model="advected_grey_optical_layer",
            optical_depth=1.2 + variant * 0.2,
            half_width_m=150.0,
            speed_m_s=10.0,
            center_s=45.0 + 5 * variant,
        )
        simulation["spatial_cloud"] = dict(direction_degrees=30.0, crosswind_width_m=1500.0)
    elif code == "N04":
        tasks = [command(20, 8.0 + variant)]
    elif code == "N05":
        # Same communication region, independent electrical feeders; PV03 unselected.
        simulation["regions"] = simulation["regions"][:1]
        for index, site in enumerate(simulation["protocol"]["stations"], 2):
            site.update(region_id="R1", address=f"10.204.1.{index}")
        tasks = [
            dict(
                kind="dispatch",
                schedule_id="task_1",
                at_s=20,
                jitter_s=3,
                station_ids=["PV01", "PV02"],
                allocation="capacity" if variant == 0 else "observed_available",
                total_kw=35.0 + variant * 5,
                batch_size=1,
                batch_interval_s=1.0 + variant,
            )
        ]
    elif code == "N06":
        tasks = [command(20, 8.0 + variant), command(100, 30.0, "task_2")]
    elif code == "N07":
        simulation["weather"]["source_start"] = f"2024-06-{4 + variant:02d}T04:00:00Z"
        tasks = [command(20, 25.0 + variant)]
    elif code == "N08":
        tasks = [
            dict(
                kind="interrogation",
                schedule_id="task_1",
                at_s=20,
                jitter_s=3,
                station_ids=["PV01", "PV02"],
            )
        ]
    elif code == "N09":
        simulation["device_operations"] = [
            dict(station_id="PV01", at_s=10, enabled=False),
            dict(station_id="PV01", at_s=40, enabled=True),
        ]
        tasks = [command(20, 10.0 + variant)]
    elif code == "N10":
        simulation["protocol"].update(
            release_after_s=25,
            interruption=dict(station_id="PV01", start_s=40, duration_s=20, planned=True),
        )
    return ScenarioConfig.model_validate(
        dict(
            schema_version="0.4-scenario.1",
            purpose="normal_scenario_development",
            # V1.3 §14.1: the same original weather date shares a source group,
            # including different normal business intents and cloud perturbations.
            source_group_id=(
                f"weather_{simulation['weather']['sha256'][:12]}_"
                + simulation["weather"]["source_start"][:10].replace("-", "")
            ),
            normal_scene=code,
            simulation=simulation,
            business_schedule=tasks,
        )
    )
