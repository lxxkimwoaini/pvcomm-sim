"""Explicit V0.3 development assumptions; not formal experiment parameters."""

from pathlib import Path
from typing import Any

from pvcomm.config.multisite import V03Config
from pvcomm.config.power import load_power_config


def configuration(count: int, duration: int, *, interruption: bool = False) -> dict[str, Any]:
    baseline = load_power_config(Path("configs/v0.2-clear.yaml"))
    data = baseline.model_dump(mode="json")
    data.update(
        schema_version="0.3", contract_revision=2, purpose="multisite_power_protocol_development"
    )
    data.pop("feeder")
    lab = data["protocol"]
    lab.update(
        run_id=f"v03_{count:02d}_{'disconnect' if interruption else 'normal'}_001",
        duration_s=duration,
        command_after_s=10,
        release_after_s=60,
        command_kw=10.0,
    )
    data["repeat_command_after_s"] = 35
    data["weather"]["cloud_shadow"] = dict(
        model="advected_grey_optical_layer",
        optical_depth=1.5,
        half_width_m=200.0,
        speed_m_s=10.0,
        center_s=80.0,
    )
    data["spatial_cloud"] = dict(direction_degrees=30.0, crosswind_width_m=1500.0)
    data["regions"] = [{"region_id": f"R{i}"} for i in range(1, 4)]
    data["feeders"] = [
        {
            "feeder_id": f"F{i}",
            "parameters": {
                **baseline.feeder.model_dump(mode="json"),
                "lv_kv": 10.0,
                "transformer_mva": 2.5,
                "line_length_km": 0.2,
                "line_r_ohm_km": 0.4,
                "line_x_ohm_km": 0.3,
                "line_max_i_ka": 0.3,
            },
        }
        for i in range(1, 4)
    ]
    stations: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    for index in range(count):
        region = min(2, index * 3 // count) + 1
        ordinal = sum(s["region_id"] == f"R{region}" for s in stations) + 2
        capacity = float((30, 100, 300, 50, 200, 500, 20, 150, 80, 250)[index % 10])
        site = baseline.protocol.stations[0].model_dump(mode="json")
        site.update(
            station_id=f"PV{index + 1:02d}",
            region_id=f"R{region}",
            common_address=index + 1,
            port=2404,
            capacity_kw=capacity,
            available_kw=capacity,
            initial_setpoint_kw=capacity,
            ramp_kw_per_s=capacity * 0.1,
            telemetry_period_s=(2, 5, 10)[index % 3],
            voltage_kv=10.0,
            feeder_id=f"F{index % 3 + 1}",
            address=f"10.204.{region}.{ordinal}",
            x_m=float(index % 10 * 100),
            y_m=float(index // 10 * 300),
            telemetry_phase_s=float(index % 2),
            deadband_fraction=0.01,
        )
        stations.append(site)
        device = baseline.devices[0].model_dump(mode="json")
        device.update(
            station_id=site["station_id"],
            rated_kw=capacity,
            rated_kva=capacity * 1.1,
            initial_setpoint_kw=capacity,
            ramp_up_kw_s=capacity * 0.1,
            ramp_down_kw_s=capacity * 0.1,
            q_ramp_kvar_s=capacity * 0.05,
            q_target_kvar=capacity * 0.05,
            tilt_degrees=float(20 + index % 5 * 5),
        )
        devices.append(device)
    lab["stations"], data["devices"] = stations, devices
    if duration >= 360 and not interruption:
        capacity = sum(s["capacity_kw"] for s in stations)
        data["dispatch_tasks"] = [
            dict(
                dispatch_id=f"area_{index}",
                at_s=at,
                region_ids=["R1", "R2", "R3"],
                total_kw=total,
                allocation=allocation,
                batch_size=5,
                batch_interval_s=1.0,
            )
            for index, (at, total, allocation) in enumerate(
                (
                    (30, capacity * 0.45, "capacity"),
                    (130, capacity, "observed_available"),
                    (230, capacity, "capacity"),
                ),
                1,
            )
        ]
    if interruption:
        lab["interruption"] = dict(station_id="PV01", start_s=65, duration_s=20, planned=True)
    return V03Config.model_validate(data).model_dump(mode="json")
