"""Experiment quality is independent of normal-business success and labels.

Normal QC remains the CLI default. This explicit profile classifies every
check, fails closed on unknown checks, and retains all original failures.
It cannot certify fault application or labels that have not been evaluated.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.qc.power import evaluate_power
from pvcomm.qc.v01 import evaluate_run

VERSION = "v04-experiment-quality-v1"
PROTOCOL_INTEGRITY = {
    "completed_and_cleaned",
    "configuration_integrity",
    "event_identity_and_causality",
    "configured_real_duration",
}
PROTOCOL_QUALITY = {
    "raw_integrity",
    "isolated_capture_topology",
    "zero_capture_drops",
    "complete_apdu_parse_rate",
    "business_callback_correspondence",
    "log_capture_alignment",
    "protocol_model_power_bounds",
    "parquet_export",
}
PROTOCOL_BUSINESS = {"three_station_business_loop", "interruption_and_recovery"}
POWER_QUALITY = {
    "configuration",
    "weather_provenance",
    "electrical_bounds",
    "ramp_and_physical_override",
    "reactive_ramp_and_capability_override",
    "historical_pvlib_recalculation",
    "accepted_commands_only",
    "weather_monotonic_mapping",
    "normal_device_operations",
    "all_powerflows_converged_recalculated",
    "power_arrow",
    "electrical_snapshot_consistency",
    "feeder_power_balance",
    "normal_network_parameters",
}
POWER_BUSINESS = {"normal_device_status_received", "electrical_operating_limits"}


def classify_quality(
    protocol: dict[str, Any], physical: dict[str, Any] | None = None
) -> dict[str, Any]:
    checks = protocol["checks"]
    known = (
        PROTOCOL_INTEGRITY | PROTOCOL_QUALITY | PROTOCOL_BUSINESS | {"dual_endpoint_correspondence"}
    )
    unexpected = set(checks) - known
    missing = (PROTOCOL_INTEGRITY | PROTOCOL_QUALITY | {"dual_endpoint_correspondence"}) - set(
        checks
    )
    integrity = {k: v for k, v in checks.items() if k in PROTOCOL_INTEGRITY}
    quality = {k: v for k, v in checks.items() if k in PROTOCOL_QUALITY}
    integrity["frozen_code"] = {"passed": protocol.get("run_code_dirty") is False}
    endpoints = checks.get("dual_endpoint_correspondence", {}).get("evidence", {})
    quality["independent_endpoint_capture_quality"] = {
        "passed": bool(endpoints)
        and all(
            e["terminal_callback_match"] and e["incomplete_bytes"] == 0 for e in endpoints.values()
        ),
        "evidence": endpoints,
        "cross_endpoint_equal_bytes_required": False,
    }
    normal_business = {k: v for k, v in checks.items() if k in PROTOCOL_BUSINESS}
    if physical is not None:
        physical_checks = physical["checks"]
        unexpected |= (
            set(physical_checks)
            - POWER_QUALITY
            - POWER_BUSINESS
            - {"independent_protocol_evidence"}
        )
        # Multisite-specific checks are optional only for the legacy 0.2 input.
        required = POWER_QUALITY - {"normal_device_operations", "normal_network_parameters"}
        missing |= required - set(physical_checks)
        quality.update(
            {f"physical/{k}": v for k, v in physical_checks.items() if k in POWER_QUALITY}
        )
        normal_business.update(
            {f"physical/{k}": v for k, v in physical_checks.items() if k in POWER_BUSINESS}
        )
    quality["check_classification_complete"] = {
        "passed": not unexpected and not missing,
        "unknown_checks": sorted(unexpected),
        "missing_checks": sorted(missing),
    }
    integrity_passed = all(v["passed"] for v in integrity.values())
    quality_passed = all(v["passed"] for v in quality.values())
    business = checks.get("three_station_business_loop", {}).get("evidence", {})
    return {
        "qc_version": VERSION,
        "run_id": protocol["run_id"],
        "run_integrity": {"passed": integrity_passed, "checks": integrity},
        "data_quality": {"passed": quality_passed, "checks": quality},
        "normal_regression": {
            "passed": protocol["passed"] and (physical is None or physical["passed"]),
            "checks": normal_business,
        },
        "command_assessments": {s: v["commands"] for s, v in business.items()},
        "transport_observations": endpoints,
        "fault_verification": {"status": "not_evaluated"},
        "label_quality": {"status": "not_evaluated"},
        "passed": integrity_passed and quality_passed,
        "scope": "experiment_integrity_and_data_quality_only_not_label_acceptance",
    }


def evaluate_experiment(root: Path) -> dict[str, Any]:
    physical = (
        evaluate_power(root, require_normal_business=False)
        if (root / "power_config.json").exists()
        else None
    )
    protocol = (
        physical["checks"]["independent_protocol_evidence"]["evidence"]
        if (physical is not None)
        else evaluate_run(root)
    )
    return classify_quality(protocol, physical)


def qc_experiment(root: Path) -> dict[str, Any]:
    destination = root / "qc" / VERSION
    if destination.exists():
        raise FileExistsError(f"experiment QC exists: {destination}")
    report = evaluate_experiment(root)
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".experiment-qc-", dir=destination.parent) as temp:
        stage = Path(temp) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return report
