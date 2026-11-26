"""Synthetic metadata-only fixtures, never formal simulation evidence."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from pvcomm.config.fault_scenes import a01_scenario
from pvcomm.config.faults import StationScope
from pvcomm.config.scenes import canonical_hash
from pvcomm.qc import fault


def test_fault_evaluator_identifies_loaded_code_outside_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = a01_scenario(StationScope(kind="station", station_ids=("PV01",)), 5)
    folder = tmp_path / "truth/fault"
    folder.mkdir(parents=True)
    source = folder / "source.yaml"
    source.write_text(config.model_dump_json(), encoding="utf-8")
    (folder / "effective.json").write_text(config.model_dump_json(), encoding="utf-8")
    (folder / "events.jsonl").write_bytes(b"")
    (tmp_path / "truth/scenario").mkdir()
    (tmp_path / "truth/scenario/effective.json").write_text(
        config.background.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "power_config.json").write_text(
        config.background.simulation.model_dump_json(), encoding="utf-8"
    )
    manifest = {
        "run_id": config.background.simulation.protocol.run_id,
        "code_version": "synthetic_run_revision",
        "fault_contract": {
            "version": config.schema_version,
            "scope": "Oracle",
            "effective_sha256": canonical_hash(config.model_dump(mode="json")),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "events_sha256": hashlib.sha256(b"").hexdigest(),
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Isolate version provenance; this fixture deliberately has no physical or
    # protocol evidence and must never pass the fault mechanism check.
    monkeypatch.setattr(fault, "audit_operations", lambda *args: {"passed": False})
    monkeypatch.setattr(fault, "evaluate_experiment", lambda *args: {"passed": True})
    repository = Path(fault.__file__).resolve().parents[3]
    expected = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True)
    )
    monkeypatch.chdir(tmp_path)
    report = fault.evaluate_fault(tmp_path)
    assert report["evaluation_code_version"] == expected
    assert report["evaluation_code_dirty"] == dirty
    assert report["run_code_version"] == "synthetic_run_revision"
    assert report["fault_verification"]["configuration_integrity"]
    assert not report["passed"]
