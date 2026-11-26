"""Read-only final queue audit, including valid members of unsuccessful pairs."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pvcomm.config.batch import BatchConfig, PairJob
from pvcomm.config.scenes import canonical_hash
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import file_sha256
from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION
from pvcomm.qc.pairs import VERSION as PAIR_QC_VERSION
from pvcomm.scenario.batch import validate_resume

VERSION = "v04-batch-audit-v1"


def member_quality(directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    identity = record["run_id"]
    if record["run_dir"] != identity or Path(identity).name != identity:
        raise ValueError("unsafe member identity")
    root = directory / identity
    if root.is_symlink() or not root.resolve().is_relative_to(directory.resolve()):
        raise ValueError("member path escapes pair")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else {}
    reports = {
        p.parent.name: json.loads(p.read_bytes()) for p in (root / "qc").glob("*/report.json")
    }
    label_qc = reports.get(LABEL_QC_VERSION, {})
    labels_path = root / "labels" / LABEL_VERSION / "event_labels.jsonl"
    label_hash_matches = bool(
        labels_path.is_file() and label_qc.get("label_sha256") == file_sha256(labels_path)
    )
    data_valid = bool(
        record.get("status") == "completed"
        and record.get("mechanism_or_normal_qc") is True
        and record.get("label_qc") is True
        and manifest.get("run_id") == identity
        and manifest.get("status") == "completed"
        and manifest.get("code_dirty") is False
        and manifest.get("cleanup_errors") == []
        and len(reports) >= 2
        and all(r.get("passed") is True for r in reports.values())
        and label_hash_matches
        and label_qc.get("invalid_labels") == 0
    )
    negative = False
    if data_valid:
        negative, nonempty = True, False
        with labels_path.open("rb") as stream:
            for line in stream:
                row = json.loads(line)
                nonempty = True
                negative &= (
                    row["run_id"] == identity
                    and row["label_status"] == "valid"
                    and row["service_violation"] is False
                )
        negative &= nonempty
    return dict(
        member=record["member"],
        run_id=identity,
        data_quality_passed=data_valid,
        valid_normal_negative=negative if record["member"] == "normal" else None,
        uncertain_labels=label_qc.get("uncertain_labels"),
        invalid_labels=label_qc.get("invalid_labels"),
        failed_qc_versions=[name for name, r in reports.items() if r.get("passed") is not True],
        label_hash_matches=label_hash_matches,
        cleanup_errors=manifest.get("cleanup_errors"),
        ledger_status=record["status"],
    )


def audit_batch(root: Path, output: Path) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError("audit must use a separate new output directory")
    matrix_path, manifest_path = root / "matrix.json", root / "manifest.json"
    original_hash = file_sha256(manifest_path)
    state = json.loads(manifest_path.read_bytes())
    config = BatchConfig.model_validate_json(matrix_path.read_bytes())
    if state["matrix_sha256"] != canonical_hash(config.model_dump(mode="json")):
        raise ValueError("matrix hash changed")
    if state["status"] not in ("completed", "completed_with_failures"):
        raise ValueError("final audit requires a finished queue")
    if any(a["status"] == "running" for t in state["tasks"] for a in t["attempts"]):
        raise ValueError("final audit cannot include a running attempt")
    validate_resume(config, root, state)
    counts: dict[str, dict[str, int]] = {}
    attempts = []
    seen = set()
    for task, spec in zip(state["tasks"], config.tasks, strict=True):
        if not isinstance(spec, PairJob):
            continue
        own = counts.setdefault(
            spec.pair.pair_type,
            dict(
                attempted_pairs=0,
                valid_pairs=0,
                business_contrast_pairs=0,
                matched_pairs=0,
                valid_normal_member_runs=0,
                valid_changed_member_runs=0,
                valid_normal_negative_runs=0,
                invalid_or_incomplete_member_runs=0,
            ),
        )
        for attempt in task["attempts"]:
            own["attempted_pairs"] += 1
            directory = root / attempt["directory"]
            ledgers = list(directory.glob("pair_*/pair-manifest.json"))
            if len(ledgers) > 1:
                raise ValueError("one pair attempt cannot contain multiple ledgers")
            members = []
            data_valid, contrast, matched = False, False, False
            if ledgers:
                pair_root = ledgers[0].parent
                ledger = json.loads(ledgers[0].read_bytes())
                for record in ledger["members"]:
                    if record["run_id"] in seen:
                        raise ValueError("duplicate member run identity across attempts")
                    seen.add(record["run_id"])
                    member = member_quality(pair_root, record)
                    members.append(member)
                    if member["data_quality_passed"]:
                        own[f"valid_{record['member']}_member_runs"] += 1
                        own["valid_normal_negative_runs"] += member["valid_normal_negative"] is True
                    else:
                        own["invalid_or_incomplete_member_runs"] += 1
                qc_path = pair_root / "qc" / PAIR_QC_VERSION / "report.json"
                if qc_path.exists():
                    quality = json.loads(qc_path.read_bytes())
                    data_valid = (
                        quality["data_quality_passed"]
                        and all(r["data_quality_passed"] for r in members)
                        and len(members) == 2
                    )
                    contrast = bool(data_valid and quality["contrast_observed"])
                similarity = pair_root / "qc/v04-traffic-similarity-v1/report.json"
                if similarity.exists():
                    matched = bool(
                        data_valid
                        and contrast
                        and json.loads(similarity.read_bytes())["hard_pair_selected"]
                    )
            own["valid_pairs"] += int(data_valid)
            own["business_contrast_pairs"] += int(contrast)
            own["matched_pairs"] += int(matched)
            attempts.append(
                dict(
                    task_id=task["task_id"],
                    directory=attempt["directory"],
                    status=attempt["status"],
                    error=attempt.get("error"),
                    data_quality_passed=bool(data_valid),
                    business_contrast=contrast,
                    matched=matched,
                    members=members,
                )
            )
    if file_sha256(manifest_path) != original_hash:
        raise ValueError("queue changed during final audit")
    repository = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    report = dict(
        audit_version=VERSION,
        created_at=datetime.now(UTC).isoformat(),
        evaluation_code_version=sha,
        evaluation_code_dirty=dirty,
        scope="Oracle_offline_final_audit_never_M2_features",
        source_code_version=state["code_version"],
        source_manifest_sha256=original_hash,
        source_matrix_file_sha256=file_sha256(matrix_path),
        matrix_sha256=state["matrix_sha256"],
        artifact_checksums_verified=True,
        queue_status=state["status"],
        queue_coverage=state["coverage"],
        pairs=counts,
        attempts=attempts,
        member_counts_are_separate_from_task_coverage=True,
        thresholds_changed=False,
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
