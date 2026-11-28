"""Synthetic provenance fixtures, never substitutes for an executed pair audit."""

import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import file_sha256
from pvcomm.qc.batch import audit_batch, member_quality
from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION


def member_fixture(root: Path, identity: str = "run_fixture", **changes: Any) -> dict[str, Any]:
    directory = root / identity
    labels = directory / "labels" / LABEL_VERSION / "event_labels.jsonl"
    labels.parent.mkdir(parents=True)
    labels.write_text(
        json.dumps(dict(run_id=identity, label_status="valid", service_violation=False) | changes)
        + "\n"
    )
    (directory / "manifest.json").write_text(
        json.dumps(dict(run_id=identity, status="completed", code_dirty=False, cleanup_errors=[]))
    )
    qc = directory / "qc" / LABEL_QC_VERSION
    qc.mkdir(parents=True)
    (qc / "report.json").write_text(
        json.dumps(
            dict(
                passed=True, invalid_labels=0, uncertain_labels=0, label_sha256=file_sha256(labels)
            )
        )
    )
    mechanism = directory / "qc/synthetic-unit-fixture"
    mechanism.mkdir()
    (mechanism / "report.json").write_text('{"passed": true}')
    return dict(
        member="normal",
        run_id=identity,
        run_dir=identity,
        status="completed",
        mechanism_or_normal_qc=True,
        label_qc=True,
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(label_status="uncertain", service_violation=None),
        dict(label_status="invalid", service_violation=None),
        dict(service_violation=True),
        dict(run_id="cross_run"),
    ],
)
def test_unknown_or_wrong_business_result_is_never_a_normal_negative(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    record = member_fixture(tmp_path, **changes)
    assert member_quality(tmp_path, record)["valid_normal_negative"] is False


def test_member_audit_requires_hash_quality_completion_and_cleanup(tmp_path: Path) -> None:
    record = member_fixture(tmp_path)
    assert member_quality(tmp_path, record)["data_quality_passed"] is True
    assert member_quality(tmp_path, record)["valid_normal_negative"] is True
    record["status"] = "running"
    assert member_quality(tmp_path, record)["data_quality_passed"] is False
    record["status"] = "completed"
    manifest = tmp_path / record["run_id"] / "manifest.json"
    value = json.loads(manifest.read_bytes())
    value["cleanup_errors"] = ["synthetic worker required kill"]
    manifest.write_text(json.dumps(value))
    assert member_quality(tmp_path, record)["data_quality_passed"] is False
    value["cleanup_errors"] = []
    manifest.write_text(json.dumps(value))
    path = tmp_path / record["run_id"] / "labels" / LABEL_VERSION / "event_labels.jsonl"
    path.write_text(path.read_text() + "\n")
    assert member_quality(tmp_path, record)["label_hash_matches"] is False
    record["run_dir"] = "../escape"
    with pytest.raises(ValueError, match="identity"):
        member_quality(tmp_path, record)


def test_audit_rejects_running_queue_before_creating_output(tmp_path: Path) -> None:
    from pvcomm.config.batch import BatchConfig, NormalJob
    from pvcomm.config.normal_scenes import normal_scenario
    from pvcomm.config.scenes import canonical_hash

    root = tmp_path / "batch"
    root.mkdir()
    config = BatchConfig(
        matrix_id="unit_fixture",
        tasks=(NormalJob(kind="normal", task_id="task_1", scenario=normal_scenario("N09")),),
    )
    (root / "matrix.json").write_text(config.model_dump_json())
    (root / "manifest.json").write_text(
        json.dumps(
            dict(status="running", matrix_sha256=canonical_hash(config.model_dump(mode="json")))
        )
    )
    output = tmp_path / "audit"
    with pytest.raises(ValueError, match="finished"):
        audit_batch(root, output)
    assert not output.exists()


def test_failed_pair_retains_valid_normal_member_without_claiming_a_valid_pair(
    tmp_path: Path,
) -> None:
    from pvcomm.config.batch import BatchConfig, MatchingInputs, PairJob
    from pvcomm.config.pairs import development_pair
    from pvcomm.config.scenes import canonical_hash
    from pvcomm.scenario.batch import artifact_inventory

    source = Path(__file__).resolve().parents[2]
    matching = MatchingInputs.model_validate(
        dict(
            policy=json.loads((source / "configs/v0.4-similarity-policy.json").read_bytes()),
            calibration=json.loads(
                (source / "configs/v0.4-similarity-calibration-dev.json").read_bytes()
            ),
        )
    )
    config = BatchConfig(
        matrix_id="synthetic_pair_audit",
        matching=matching,
        tasks=(
            PairJob(kind="pair", task_id="task_1", pair=development_pair("P03", "pair_fixture")),
        ),
    )
    root = tmp_path / "batch"
    attempt = root / "task_1/attempt_1"
    pair = attempt / "pair_fixture"
    normal = member_fixture(pair, "run_normal")
    changed = member_fixture(pair, "run_changed")
    changed.update(member="changed", status="running", mechanism_or_normal_qc=False)
    (pair / "pair-manifest.json").write_text(json.dumps(dict(members=[normal, changed])))
    state = dict(
        status="completed_with_failures",
        code_version="synthetic_fixture",
        matrix_sha256=canonical_hash(config.model_dump(mode="json")),
        coverage={},
        tasks=[
            dict(
                task_id="task_1",
                kind="pair",
                status="failed",
                attempts=[
                    dict(
                        status="failed",
                        directory="task_1/attempt_1",
                        artifacts=artifact_inventory(attempt),
                    )
                ],
            )
        ],
    )
    (root / "matrix.json").write_text(config.model_dump_json())
    (root / "manifest.json").write_text(json.dumps(state))
    report = audit_batch(root, tmp_path / "audit")
    counts = report["pairs"]["P03"]
    assert counts["attempted_pairs"] == 1 and counts["valid_pairs"] == 0
    assert counts["valid_normal_member_runs"] == counts["valid_normal_negative_runs"] == 1
    assert counts["valid_changed_member_runs"] == 0
    assert counts["invalid_or_incomplete_member_runs"] == 1
    assert counts["business_contrast_pairs"] == counts["matched_pairs"] == 0
    (pair / "run_normal/manifest.json").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        audit_batch(root, tmp_path / "tampered-audit")
    assert not (tmp_path / "tampered-audit").exists()
