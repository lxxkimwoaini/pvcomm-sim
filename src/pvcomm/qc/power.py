"""Post-run physical QC; consumes Oracle evidence only in this module."""

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pvcomm.config.multisite import V03Config
from pvcomm.config.power import power_document
from pvcomm.power.feeder import FeederNetwork
from pvcomm.power.runtime import weather_directory
from pvcomm.power.solar import SolarModel
from pvcomm.qc.electrical import classify_feeder
from pvcomm.qc.performance import records
from pvcomm.qc.v01 import evaluate_run
from pvcomm.storage.parquet import read_jsonl
from pvcomm.storage.power_parquet import verify_power_parquet
from pvcomm.weather import HistoricalWeather, station_weather

VERSION = "v02-power-v7"
MULTISITE_VERSION = "v03-power-v5"
TOLERANCE = 1e-6


def evaluate_power(root: Path, *, require_normal_business: bool = True) -> dict[str, Any]:
    config = power_document(root / "power_config.json")
    manifest = json.loads((root / "manifest.json").read_text())
    rows = [
        r
        for r in read_jsonl(list((root / "truth").glob("electrical_state*.jsonl")))
        if r.get("source") == "pvlib_pvwatts_inverter_v02"
    ]
    terminal = read_jsonl(list((root / "truth").glob("terminal_events*.jsonl")))
    flow = [r for r in terminal if r["event_type"] == "terminal_powerflow_solved"]
    checks: dict[str, Any] = {}

    def check(name: str, passed: bool, evidence: Any) -> None:
        checks[name] = {"passed": bool(passed), "evidence": evidence}

    check(
        "configuration",
        power_document(root / "scenario.yaml") == config
        and manifest["power_config_sha256"]
        == hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
        {"weather_sha256": config.weather.sha256, "basis": config.parameter_basis},
    )
    if isinstance(config, V03Config):
        expected_links = 2 * (
            sum(r.uplink is not None for r in config.regions)
            + sum(s.access_link is not None for s in config.protocol.stations)
        )
        initial = manifest["network"].get("netem_initial", [])
        final = manifest["network"].get("netem_final", [])
        check(
            "normal_network_parameters",
            len(initial) == len(final) == expected_links
            and all(r["actual"]["drops"] == 0 for r in final),
            {
                "bidirectional_qdiscs": expected_links,
                "readback_verified_by_runner": True,
                "qdisc_drops": sum(r["actual"]["drops"] for r in final),
                "basis": "synthetic normal link delay/jitter; no configured packet loss",
            },
        )
    source = json.loads((root / "truth/weather/source.json").read_text())
    source_valid = all(
        hashlib.sha256((root / "truth/weather" / name).read_bytes()).hexdigest() == record["sha256"]
        for name, record in source["files"].items()
    )
    check(
        "weather_provenance",
        source_valid and source["canonical_sha256"] == config.weather.sha256,
        {
            "header": source["header"],
            "usage_basis": source["usage_basis"],
            "source_kind": source["source_kind"],
            "sampling": config.weather.sampling,
        },
    )
    bounds, ramps, models, causal, timing = True, True, True, True, True
    errors = []
    q_ramps = True
    device_status_valid = True
    for device in config.devices:
        own = sorted(
            (r for r in rows if r["station_id"] == device.station_id),
            key=lambda r: r["model_mono_ns"],
        )
        weather_config = station_weather(config, device.station_id)
        weather_root = weather_directory(root, config, device.station_id)
        weather = HistoricalWeather(weather_root / "weather.json", weather_config)
        station_source = json.loads((weather_root / "source.json").read_text())
        if station_source["canonical_sha256"] != weather_config.sha256 or not all(
            hashlib.sha256((weather_root / name).read_bytes()).hexdigest() == record["sha256"]
            for name, record in station_source["files"].items()
        ):
            raise ValueError("station weather provenance mismatch")
        model = SolarModel(device, weather_config)
        local_operations = sorted(
            (
                e
                for e in terminal
                if e["event_type"] == "terminal_local_device_operation"
                and e["station_id"] == device.station_id
            ),
            key=lambda e: e["ts_mono_ns"],
        )
        previous = None
        if not own:
            bounds = ramps = models = causal = False
        for row in own:
            p, q, avail = row["p_actual_kw"], row["q_actual_kvar"], row["p_avail_kw"]
            bounds &= (
                0 <= p <= min(avail, device.rated_kw) + TOLERANCE
                and p * p + q * q <= device.rated_kva**2 + TOLERANCE
                and 0 <= row["p_set_accepted_kw"] <= device.rated_kw
            )
            if not row["running"]:
                bounds &= abs(p) + abs(q) <= TOLERANCE
            if previous is not None:
                dt = (row["model_mono_ns"] - previous["model_mono_ns"]) / 1e9
                ramps &= p - previous["p_actual_kw"] <= device.ramp_up_kw_s * dt + TOLERANCE
                # Only physical shortage/disconnection permits a faster drop.
                drop = previous["p_actual_kw"] - p
                ramps &= (
                    drop <= device.ramp_down_kw_s * dt + TOLERANCE
                    or row["constraint_reason"] == "availability_or_disconnect"
                    and (not row["running"] or abs(p - avail) <= TOLERANCE)
                )
                q_change = abs(q - previous["q_actual_kvar"])
                q_cap = max(0, device.rated_kva**2 - p**2) ** 0.5
                q_ramps &= (
                    q_change <= device.q_ramp_kvar_s * dt + TOLERANCE
                    or not row["running"]
                    or abs(abs(q) - q_cap) <= TOLERANCE
                    and abs(previous["q_actual_kvar"]) > q_cap
                )
                if row["p_set_accepted_kw"] != previous["p_set_accepted_kw"]:
                    accepts = [
                        e
                        for e in terminal
                        if e["event_type"] == "terminal_model_target_accepted"
                        and e["station_id"] == device.station_id
                        and abs(e["target_kw"] - row["p_set_accepted_kw"]) <= 0.1
                        and previous["ts_mono_ns"] <= e["ts_mono_ns"] <= row["ts_mono_ns"]
                    ]
                    causal &= len(accepts) == 1
            elapsed = (
                datetime.fromisoformat(row["source_weather_ts"]) - weather_config.source_start
            ).total_seconds()
            mapped_elapsed = (
                max(
                    0,
                    row["model_mono_ns"]
                    - manifest.get(
                        "scene_clock", {"start_mono_ns": manifest["clock_origin"]["ts_mono_ns"]}
                    )["start_mono_ns"],
                )
                / 1e9
            )
            timing &= abs(mapped_elapsed - elapsed) <= 1e-6
            if isinstance(config, V03Config):
                enabled = device.enabled
                for event in local_operations:
                    if event["ts_mono_ns"] <= row["ts_mono_ns"]:
                        enabled = event["enabled"]
                device_status_valid &= row.get("enabled", device.enabled) == enabled
                device_status_valid &= row["running"] == (
                    enabled and device.connected and not device.faulted
                )
            expected_weather = weather.sample(mapped_elapsed)
            models &= all(
                abs(row[field] - getattr(expected_weather, field)) <= TOLERANCE
                for field in (
                    "ghi_w_m2",
                    "dni_w_m2",
                    "dhi_w_m2",
                    "temp_air_c",
                    "wind_speed_m_s",
                    "source_ghi_w_m2",
                    "optical_transmittance",
                )
            )
            expected = model.calculate(expected_weather)
            error = max(abs(row[field] - expected[field]) for field in expected)
            errors.append(error)
            models &= error < TOLERANCE
            previous = row
    check(
        "electrical_bounds",
        bounds and bool(rows),
        {"samples": len(rows), "absolute_tolerance": TOLERANCE},
    )
    check("ramp_and_physical_override", ramps, "up/down ramp; resource/disconnect override only")
    check(
        "reactive_ramp_and_capability_override",
        q_ramps,
        "Q slew; disconnect or enforced P-priority capability circle only",
    )
    check(
        "historical_pvlib_recalculation",
        models and bool(rows),
        {"max_absolute_error": max(errors, default=None)},
    )
    accepted = [r for r in terminal if r["event_type"] == "terminal_command_accepted"]
    model_accepts = [r for r in terminal if r["event_type"] == "terminal_model_target_accepted"]
    causal &= len(accepted) == len(model_accepts)
    if require_normal_business:
        causal &= len(accepted) == (
            sum(
                sum(s.region_id in t.region_ids for s in config.protocol.stations)
                for t in config.dispatch_tasks
            )
            if isinstance(config, V03Config) and config.dispatch_tasks
            else len(config.devices) * (2 + int(config.repeat_command_after_s is not None))
        )
    used_model_accepts: set[str] = set()
    for acceptance in accepted:
        matches = [
            e
            for e in model_accepts
            if e["station_id"] == acceptance["station_id"]
            and e["target_kw"] == acceptance["target_kw"]
            and 0 <= acceptance["ts_mono_ns"] - e["ts_mono_ns"] < 100_000_000
        ]
        causal &= len(matches) == 1 and matches[0]["event_id"] not in used_model_accepts
        if len(matches) == 1:
            used_model_accepts.add(matches[0]["event_id"])
    check(
        "accepted_commands_only",
        causal,
        "model acceptance must pair with actual c104 terminal acceptance",
    )
    check(
        "weather_monotonic_mapping",
        timing,
        {"source_time_tolerance_s": 1e-6, "weather_value_tolerance": TOLERANCE},
    )
    if isinstance(config, V03Config):
        operations = sorted(
            (e for e in terminal if e["event_type"] == "terminal_local_device_operation"),
            key=lambda e: (e["station_id"], e["at_s"]),
        )
        expected_ops = sorted(config.device_operations, key=lambda e: (e.station_id, e.at_s))
        device_status_valid &= len(operations) == len(expected_ops)
        for event, expected_op in zip(operations, expected_ops, strict=False):
            device_status_valid &= (
                all(event[k] == v for k, v in expected_op.model_dump().items())
                and 0 <= event["applied_scene_s"] - expected_op.at_s < 1.5
            )
        check(
            "normal_device_operations",
            device_status_valid,
            {"operations": len(operations), "independent_of_communication_and_faults": True},
        )
        status_observations = [
            e
            for e in records((root / "observed").glob("master_events*.jsonl"))
            if e["event_type"] == "master_observed_point" and e.get("ioa") == 2001
        ]
        status_delays = []
        for operation in operations:
            device = next(d for d in config.devices if d.station_id == operation["station_id"])
            expected_running = operation["enabled"] and device.connected and not device.faulted
            matches = [
                e
                for e in status_observations
                if e["station_id"] == operation["station_id"]
                and e["value"] == expected_running
                and 0 <= e["ts_mono_ns"] - operation["ts_mono_ns"] < 3_000_000_000
            ]
            if matches:
                status_delays.append(
                    min(e["ts_mono_ns"] for e in matches) - operation["ts_mono_ns"]
                )
        check(
            "normal_device_status_received",
            len(status_delays) == len(operations),
            {
                "operations": len(operations),
                "received": len(status_delays),
                "delay_ms": [d / 1e6 for d in status_delays],
                "evidence": "actual master IOA2001; independent raw protocol QC checks PCAP",
            },
        )
    feeder = FeederNetwork(config)
    groups: dict[int, dict[str, Any]] = {}
    for row in flow:
        groups.setdefault(row["solved_mono_ns"], {})[row["station_id"]] = row
    flow_ok, flow_error = bool(groups), []
    diagnostics = []
    temporal_ok = True
    for group in groups.values():
        calculated = feeder.solve(group)
        for feeder_id, values in feeder.diagnostic_rows.items():
            diagnostics.append(
                {
                    "feeder_id": feeder_id,
                    "solved_mono_ns": next(iter(group.values()))["solved_mono_ns"],
                    **values,
                    **classify_feeder(values),
                }
            )
        stamps = [r["injection_mono_ns"] for r in group.values()]
        temporal_ok &= max(stamps) - min(stamps) <= 1_500_000_000
        for station, row in group.items():
            error = max(abs(row[k] - calculated[station][k]) for k in calculated[station])
            flow_error.append(error)
            flow_ok &= row["converged"] and error <= TOLERANCE
            flow_ok &= row["injection_mono_ns"] <= row["solved_mono_ns"]
            temporal_ok &= 0 <= row["solved_mono_ns"] - row["injection_mono_ns"] <= 3_000_000_000
    flow_ok &= not any(r["event_type"] == "terminal_powerflow_failed" for r in terminal)
    check(
        "all_powerflows_converged_recalculated",
        flow_ok,
        {
            "solves": len(groups),
            "rows": len(flow),
            "max_absolute_error": max(flow_error, default=None),
        },
    )
    check(
        "power_arrow", verify_power_parquet(root), "0.2-power-evidence.2; Oracle-only typed tables"
    )
    check(
        "electrical_snapshot_consistency",
        temporal_ok and bool(groups),
        {"maximum_age_ms": 3000, "maximum_span_ms": 1500},
    )
    check(
        "feeder_power_balance",
        bool(diagnostics) and all(r["calculation_valid"] for r in diagnostics),
        {"balance_tolerance_kw_kvar": 0.001, "rows": diagnostics},
    )
    check(
        "electrical_operating_limits",
        bool(diagnostics) and all(not r["electrical_violations"] for r in diagnostics),
        "Voltage [0.9,1.1]pu, thermal <=100%; engineering development limits. "
        "Failures are electrical, never communication labels; reverse flow is operating context.",
    )
    protocol = evaluate_run(root)
    check("independent_protocol_evidence", protocol["passed"], protocol)
    return {
        "qc_version": MULTISITE_VERSION if isinstance(config, V03Config) else VERSION,
        "run_id": config.protocol.run_id,
        "checks": checks,
        "passed": all(c["passed"] for c in checks.values()),
        "limitations": (
            "Historical hourly background; optional modeled optical layer; "
            "no measured sub-hour cloud truth."
        ),
    }


def qc_power(root: Path) -> dict[str, Any]:
    config = power_document(root / "power_config.json")
    destination = root / "qc" / (MULTISITE_VERSION if isinstance(config, V03Config) else VERSION)
    if destination.exists():
        raise FileExistsError(f"power QC output exists: {destination}")
    report = evaluate_power(root)
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".power-qc-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return report
