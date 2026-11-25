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
from pvcomm.power.solar import SolarModel
from pvcomm.qc.electrical import classify_feeder
from pvcomm.qc.v01 import evaluate_run
from pvcomm.storage.parquet import read_jsonl
from pvcomm.storage.power_parquet import verify_power_parquet
from pvcomm.weather import HistoricalWeather

VERSION = "v02-power-v6"
TOLERANCE = 1e-6


def evaluate_power(root: Path) -> dict[str, Any]:
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
    source = json.loads((root / "truth/weather/source.json").read_text())
    source_valid = all(
        hashlib.sha256((root / "truth/weather" / name).read_bytes()).hexdigest() == record["sha256"]
        for name, record in source["files"].items()
    )
    weather = HistoricalWeather(root / "truth/weather/weather.json", config.weather)
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
    for device in config.devices:
        own = sorted(
            (r for r in rows if r["station_id"] == device.station_id),
            key=lambda r: r["model_mono_ns"],
        )
        model = SolarModel(device, config.weather)
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
                datetime.fromisoformat(row["source_weather_ts"]) - config.weather.source_start
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
    causal &= (
        len(accepted)
        == len(model_accepts)
        == len(config.devices) * (2 + int(config.repeat_command_after_s is not None))
    )
    for acceptance in accepted:
        matches = [
            e
            for e in model_accepts
            if e["station_id"] == acceptance["station_id"]
            and e["target_kw"] == acceptance["target_kw"]
            and 0 <= acceptance["ts_mono_ns"] - e["ts_mono_ns"] < 100_000_000
        ]
        causal &= len(matches) == 1
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
        "qc_version": VERSION,
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
    destination = root / "qc" / ("v03-power-v1" if isinstance(config, V03Config) else VERSION)
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
