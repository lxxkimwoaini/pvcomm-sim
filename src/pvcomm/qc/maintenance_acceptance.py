"""Grouped A08 development acceptance on retained runs, without rerunning physics."""

import json
import subprocess
from pathlib import Path
from typing import Any

from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.domain.labels import EventLabel
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import file_sha256, load_label_inputs
from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION
from pvcomm.qc.labels import audit_label_records
from pvcomm.qc.terminal import version_for

VERSION = "v04-grouped-recovery-acceptance-v1"


def recovery_outcomes(
    config: FaultScenarioConfig,
    maintenance: dict[str, Any],
    labels: list[dict[str, Any]],
    assessments: dict[str, list[dict[str, Any]]],
) -> bool:
    targets = set(config.target_stations())
    maintained = {e.station_id for e in config.background.expanded_maintenance()}
    all_stations = {s.station_id for s in config.background.simulation.protocol.stations}
    if (
        config.fault.fault_type != "A08"
        or not config.background.maintenance_windows
        or not maintenance["passed"]
        or set(maintenance["stations"]) != maintained
        or {r["station_id"] for r in labels} != all_stations
        or len(labels) != len(all_stations)
    ):
        return False
    for station, evidence in maintenance["stations"].items():
        if not evidence["passed"] or evidence["business_recovered_within_deadline"] is (
            station in targets
        ):
            return False
    for label in labels:
        target = label["station_id"] in targets
        if (
            label["label_status"] != "valid"
            or label["service_violation"] is not target
            or label["fault_applied"] is not target
            or (target and label["recovery_censored"])
        ):
            return False
    for station in all_stations - maintained:
        own = assessments.get(station, [])
        if not own or not all(r["outcome"] == "accepted_complete" for r in own):
            return False
    return True


def qc_grouped_recovery(root: Path) -> dict[str, Any]:
    destination = root / "qc" / VERSION
    if destination.exists():
        raise FileExistsError(f"grouped recovery acceptance exists: {destination}")
    config = FaultScenarioConfig.model_validate_json(
        (root / "truth/fault/effective.json").read_bytes()
    )
    manifest = json.loads((root / "manifest.json").read_bytes())
    mechanism_path = root / "qc" / version_for(config) / "report.json"
    label_qc_path = root / "qc" / LABEL_QC_VERSION / "report.json"
    mechanism = json.loads(mechanism_path.read_bytes())
    previous = json.loads(label_qc_path.read_bytes())
    label_path = root / "labels" / LABEL_VERSION / "event_labels.jsonl"
    label_manifest_path = label_path.parent / "manifest.json"
    metadata = json.loads(label_manifest_path.read_bytes())
    labels = [EventLabel.model_validate_json(line) for line in label_path.read_text().splitlines()]
    inputs = load_label_inputs(root)
    audit = audit_label_records(inputs, labels)
    integrity = bool(
        metadata["source_hashes"] == inputs.hashes
        and previous["label_sha256"] == metadata["label_sha256"] == file_sha256(label_path)
        and previous["label_manifest_sha256"] == file_sha256(label_manifest_path)
        and not metadata["evaluation_code_dirty"]
        and previous["passed"]
        and audit["passed"]
        and manifest["status"] == "completed"
        and not manifest["code_dirty"]
        and not manifest["cleanup_errors"]
        and mechanism["passed"]
        and mechanism["run_id"] == previous["run_id"] == manifest["run_id"]
    )
    outcomes = recovery_outcomes(
        config,
        mechanism["authorized_maintenance"],
        [r.model_dump(mode="json") for r in labels],
        mechanism["experiment_quality"]["command_assessments"],
    )
    repository = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"]))
    report = dict(
        qc_version=VERSION,
        run_id=manifest["run_id"],
        run_code_version=manifest["code_version"],
        evaluation_code_version=sha,
        evaluation_code_dirty=dirty,
        integrity=integrity,
        expected_station_outcomes=outcomes,
        independent_label_audit=audit,
        passed=integrity and outcomes and not dirty,
        source_hashes={
            p.relative_to(root).as_posix(): file_sha256(p)
            for p in (
                mechanism_path,
                label_qc_path,
                label_manifest_path,
                label_path,
                root / "manifest.json",
            )
        },
    )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
