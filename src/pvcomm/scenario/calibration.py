"""Independent normal repeats using the existing isolated runner and quality gates."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from pvcomm.config.normal_scenes import normal_scenario
from pvcomm.config.scenes import ScenarioConfig, canonical_hash, instantiate_scenario
from pvcomm.config.similarity import SimilarityPolicy
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import file_sha256, label_run
from pvcomm.pipeline.traffic import summarize_traffic
from pvcomm.qc.labels import qc_labels
from pvcomm.qc.schedule import qc_schedule
from pvcomm.qc.similarity import calibrate_thresholds, traffic_distances
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet


def calibration_scenes() -> tuple[tuple[str, ScenarioConfig], ...]:
    """Six preregistered normal backgrounds, separate from previous paired trials."""
    result = []
    for index, code in enumerate(("N01", "N09", "N10")):
        for repeat in (0, 1):
            raw = normal_scenario(code).model_dump(mode="json")
            simulation = raw["simulation"]
            simulation["protocol"]["seed"] = 4000 + 10 * index + repeat
            simulation["weather"]["source_start"] = f"2024-06-{10 + repeat:02d}T04:00:00Z"
            raw["source_group_id"] = (
                f"weather_{simulation['weather']['sha256'][:12]}_202406{10 + repeat:02d}"
            )
            for station in simulation["protocol"]["stations"]:
                station["access_link"]["seed"] += 2000 + 20 * index + 2 * repeat
            for region in simulation["regions"]:
                region["uplink"]["seed"] += 2000 + 20 * index + 2 * repeat
            result.append((f"calibration_{index}_{repeat}", ScenarioConfig.model_validate(raw)))
    return tuple(result)


def run_calibration(policy: SimilarityPolicy, output: Path) -> dict[str, Any]:
    if os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        raise RuntimeError("calibration requires the explicitly authorized isolated Linux lab")
    repository = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], text=True):
        raise ValueError("freeze clean calibration source before running")
    scenes = calibration_scenes()
    output.mkdir(parents=True, exist_ok=False)
    (output / "policy.json").write_text(policy.model_dump_json(indent=2) + "\n", encoding="utf-8")
    plan = {key: scene.model_dump(mode="json") for key, scene in scenes}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    state: dict[str, Any] = dict(
        status="running", code_version=sha, plan_sha256=canonical_hash(plan), cases=[]
    )

    def save() -> None:
        temporary = output / ".state.tmp"
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "manifest.json")

    records = []
    save()
    try:
        for name, template in scenes:
            case: dict[str, Any] = dict(case_id=name, status="running", members=[])
            state["cases"].append(case)
            save()
            summaries = []
            source_hashes = {}
            for _ in range(2):
                run_id = f"run_{uuid.uuid4().hex}"
                scene = instantiate_scenario(template, run_id)
                source = output / f"input_{run_id}.json"
                source.write_text(scene.model_dump_json(indent=2), encoding="utf-8")
                case["members"].append(run_id)
                save()
                root = run_isolated(
                    scene.simulation.protocol,
                    source,
                    output,
                    power=scene.simulation,
                    scenario=scene,
                )
                parse_run(root)
                export_parquet(root)
                export_power_parquet(root)
                normal = qc_schedule(root)
                if not normal["passed"]:
                    raise RuntimeError(f"normal calibration QC failed; preserved at {root}")
                label_run(root)
                labels = qc_labels(root)
                if (
                    not labels["passed"]
                    or labels["invalid_labels"]
                    or labels["uncertain_labels"]
                    or labels["service_violation_stations"]
                ):
                    raise RuntimeError(
                        f"calibration needs independently confirmed normal labels: {root}"
                    )
                summaries.append(
                    summarize_traffic(
                        root,
                        15,
                        115,
                        tuple(s.station_id for s in scene.simulation.protocol.stations),
                        policy,
                    )
                )
                source_hashes[run_id] = dict(
                    manifest=file_sha256(root / "manifest.json"),
                    pcap=file_sha256(root / "raw/pcap/primary.pcap"),
                    labels=file_sha256(root / "labels" / LABEL_VERSION / "event_labels.jsonl"),
                )
            record = dict(
                purpose="independent_normal_repeat_calibration",
                pair_id=name,
                run_ids=case["members"],
                source_hashes=source_hashes,
                passed=True,
                shared_sha256=canonical_hash(template.model_dump(mode="json")),
                summaries=summaries,
                distances=traffic_distances(*summaries),
            )
            records.append(record)
            with (output / "calibration-records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
            case["status"] = "completed"
            save()
        thresholds = calibrate_thresholds(policy, records)
        (output / "thresholds.json").write_text(
            thresholds.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        state.update(status="completed", thresholds_sha256=file_sha256(output / "thresholds.json"))
    except BaseException as exc:
        state.update(
            status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        save()
    return state
