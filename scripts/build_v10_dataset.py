"""Export a new immutable production snapshot through the V0.5.1 feature/QC pipeline."""

import argparse
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.pipeline.coverage import profile_records, run_bounds
from pvcomm.pipeline.dataset import (
    code_record,
    inspect_run,
    read_json,
    run_directories,
    split_records,
    write_json,
)
from pvcomm.pipeline.features import build_features
from pvcomm.qc.dataset import qc_dataset
from pvcomm.storage.parquet import iter_jsonl, source_hash


def fixed_assignments(rows: list[dict[str, Any]], protocol: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse connected provenance groups, then apply the user's pre-frozen roles."""
    previous = {r["run_id"]: r for r in protocol["development_assignment"]["runs"]}
    catalogue = {r["run_id"]: r for r in rows}
    assignments = split_records(rows, 20261127)
    keys: dict[str, set[str]] = defaultdict(set)
    for row in assignments:
        record = catalogue[row["run_id"]]
        dates = {k.rsplit(":", 1)[-1] for k in record["group_keys"] if k.startswith("weather:")}
        heldout = bool(dates.intersection(protocol["test_dates"]))
        if row["run_id"] in previous:
            if heldout:
                raise ValueError("development run overlaps held-out source")
            row["partition"] = (
                "validation" if previous[row["run_id"]]["partition"] == "validation" else "train"
            )
        else:
            if heldout and not dates.issubset(set(protocol["test_dates"])):
                raise ValueError("test segment crosses an unfrozen weather date")
            row["partition"] = "test" if heldout else "train"
        for key in [f"run:{row['run_id']}", *row["group_keys"]]:
            keys[key].add(row["partition"])
    if any(len(parts) != 1 for parts in keys.values()):
        raise ValueError("frozen train/validation/test source conflict")
    return assignments


def export_snapshot(plan: Path, production: Path, root: Path) -> dict[str, Any]:
    began = time.monotonic()
    if code_record()["code_dirty"]:
        raise ValueError("freeze clean processing source before export")
    if root.exists():
        raise FileExistsError("snapshot already exists; choose a new unique directory")
    protocol = read_json(plan / "protocol.json")
    for name, expected in protocol["batch_sha256"].items():
        if source_hash(plan / f"{name}.json") != expected:
            raise ValueError("frozen production configuration changed")
    development = Path(protocol["development_dataset"])
    if source_hash(development / "release.json") != protocol["development_release_sha256"]:
        raise ValueError("frozen development release changed")
    old = list(iter_jsonl([development / "runs.jsonl"]))
    records = []
    for record in old:
        if record.get("eligible") and record["site_count"] != 3:
            record = dict(record, eligible=False, reasons=["historical_ten_site_index_only"])
        records.append(record)
    for directory in sorted(run_directories([production])):
        try:
            record = inspect_run(directory)
        except (OSError, ValueError, KeyError) as exc:
            record = dict(path=str(directory), eligible=False, reasons=[f"incomplete:{exc}"])
        records.append(record)
    eligible = [r for r in records if r.get("eligible")]
    if len({r["run_id"] for r in eligible}) != len(eligible):
        raise ValueError("duplicate valid run identity")
    if any(r["site_count"] != 3 for r in eligible):
        raise ValueError("production export is three-site only")
    assignments = fixed_assignments(eligible, protocol)
    if not any(r["partition"] == "test" for r in assignments):
        raise ValueError("no independently QC-qualified formal test data yet")
    root.mkdir(parents=True)
    with (root / "runs.jsonl").open("x", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    write_json(
        root / "inventory.json",
        dict(
            schema_version="0.5-dataset.1",
            **code_record(),
            discovered=len(records),
            eligible=len(eligible),
            run_index_sha256=source_hash(root / "runs.jsonl"),
            excluded_reasons=dict(
                Counter(reason for r in records for reason in r.get("reasons", []))
            ),
            independent_runs_by_fault=dict(Counter(c for r in eligible for c in r["fault_types"])),
        ),
    )
    split = root / "split"
    split.mkdir()
    policy = DatasetPolicy.model_validate(
        read_json(Path(__file__).resolve().parents[1] / "configs/v0.5.1-dataset.json")
    )
    write_json(split / "assignment.json", dict(runs=assignments))
    write_json(split / "policy.json", policy.model_dump(mode="json"))
    write_json(split / "source_profile.json", profile_records(eligible))
    write_json(
        split / "stratification.json",
        dict(
            method="frozen_independent_test_no_reoptimization",
            plan_sha256=source_hash(plan / "protocol.json"),
            development_assignment_sha256=source_hash(development / "split/assignment.json"),
            note=(
                "inherited feature policy identifier retained; "
                "no development stratification search used"
            ),
            test_dates=protocol["test_dates"],
            old_test_reassigned_to="train",
        ),
    )
    write_json(
        split / "audit.json",
        dict(
            passed=True,
            **code_record(),
            grouped_before_windowing=True,
            crossed_keys=[],
            partition_runs=dict(Counter(r["partition"] for r in assignments)),
            independent_groups=len({r["group_id"] for r in assignments}),
            assignment_sha256=source_hash(split / "assignment.json"),
            inventory_sha256=source_hash(root / "inventory.json"),
            policy_sha256=source_hash(split / "policy.json"),
            profile_sha256=source_hash(split / "source_profile.json"),
            stratification_sha256=source_hash(split / "stratification.json"),
        ),
    )
    # Original evidence remains in-place. This is an index, never a duplicate PCAP copy.
    write_json(
        root / "raw_structured_index.json",
        dict(
            runs=[
                dict(
                    run_id=r["run_id"],
                    files=r["files"],
                    label_path=r["label_path"],
                    evidence_sha256=r["evidence_sha256"],
                    scene_clock=r["scene_clock"],
                    duration_s=r["duration_s"],
                    weather=r["weather"],
                )
                for r in eligible
            ],
            historical_ten_site=[
                r for r in records if "historical_ten_site_index_only" in r.get("reasons", [])
            ],
        ),
    )
    scales = {}
    for scale in ("W10", "W30", "W60"):
        build_features(root, scale=scale)
        report = qc_dataset(root, scale=scale)
        if not report["passed"]:
            raise RuntimeError(f"{scale} independent QC failed; failed snapshot retained")
        scales[scale] = dict(
            qc=report,
            statistics=read_json(root / scale / "dataset_statistics.json"),
            events=read_json(root / scale / "event_coverage.json")["by_partition"],
        )
        print(f"{scale}: QC passed, {time.monotonic() - began:.3f}s", flush=True)
    catalogue = {r["run_id"]: r for r in eligible}
    formal = [catalogue[r["run_id"]] for r in assignments if r["partition"] == "test"]
    normal = [r for r in eligible if not r["fault_types"] and not r["violation_stations"]]
    # Count independent valid runs with a service violation, not stations or windows.
    coverage = dict(
        Counter(c for r in formal if r["violation_stations"] > 0 for c in r["fault_types"])
    )
    measured = sum((run_bounds(r)[2] - run_bounds(r)[1]) / 3.6e12 for r in normal)
    result = dict(
        version="1.0",
        status="first_validated_delivery_targets_in_progress",
        evaluation="formal_test_sealed_no_models_evaluated",
        **code_record(),
        production_simulation_code=protocol["code_version"],
        protocol_sha256=source_hash(plan / "protocol.json"),
        eligible_three_site_runs=len(eligible),
        original_basis_runs=106,
        historical_ten_site_index_only=6,
        partition_runs=dict(Counter(r["partition"] for r in assignments)),
        partition_source_groups={
            p: len({r["group_id"] for r in assignments if r["partition"] == p})
            for p in ("train", "validation", "test")
        },
        independent_test_positive_runs=coverage,
        normal_postwarmup_hours=measured,
        normal_target_hours=168,
        initial_normal_production_target_hours=24,
        remaining_normal_target_hours=max(0, 168 - measured),
        scales=scales,
        elapsed_s=time.monotonic() - began,
        continuous_week=False,
    )
    write_json(root / "statistics.json", result)
    shutil.copyfile(plan / "protocol.json", root / "production_protocol.json")
    docs = Path(__file__).resolve().parents[1] / "docs"
    for name in ("v10-dataset.md", "data-dictionary-v05.md", "data-dictionary-v051.md"):
        shutil.copyfile(docs / name, root / name)
    text = [
        "# PVComm V1.0 首批正式数据",
        "",
        "独立测试已封存，不用于调参或方法选择。",
        "",
        f"有效三站运行{len(eligible)}，独立测试阳性运行{coverage}。",
        f"合格纯正常运行预热后累计{measured:.6f}小时，168小时尚未完成。",
        "历史十站仅索引，原始证据不复制、不改写。各尺度13项独立QC通过。",
        "独立日期片段具有独立初始化和真实起止，不是连续一周实测。",
        "",
        "|尺度|训练窗口|验证窗口|测试窗口|测试可评估独立运行|",
        "|---|---:|---:|---:|---:|",
    ]
    for scale, data in scales.items():
        n = data["statistics"]["windows_by_split"]
        text.append(
            f"|{scale}|{n.get('train', 0)}|{n.get('validation', 0)}|"
            f"{n.get('test', 0)}|{data['events']['test']['evaluable_event_runs']}|"
        )
    (root / "DATA_CARD.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    write_json(
        root / "release.json",
        dict(
            version="1.0",
            **code_record(),
            status=result["status"],
            files={
                str(p.relative_to(root)): source_hash(p)
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--production", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    args = parser.parse_args()
    result = export_snapshot(args.plan, args.production, args.dataset_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "scales"}))


if __name__ == "__main__":
    main()
