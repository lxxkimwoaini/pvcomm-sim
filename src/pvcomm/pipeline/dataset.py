"""Immutable run catalogue and grouping before any causal windows are constructed."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.domain.labels import EventLabel
from pvcomm.storage.parquet import iter_jsonl, source_hash

VERSION = "0.5-dataset.1"


def read_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def code_record() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    return dict(
        code_version=subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        code_dirty=bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"])),
    )


def run_directories(roots: list[Path]) -> Iterator[Path]:
    """Prune large evidence trees, never follow source symlinks or duplicate roots."""
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"inventory root does not exist: {root}")
        for folder, directories, files in os.walk(root, followlinks=False):
            directories[:] = [
                d
                for d in directories
                if d
                not in {
                    "parsed",
                    "truth",
                    "observed",
                    "raw",
                    "qc",
                    "labels",
                    ".git",
                    "node_modules",
                    "sources",
                    ".venv",
                    ".venv-wsl",
                }
                and not d.startswith("v03_source")
            ]
            if "manifest.json" not in files:
                continue
            path = Path(folder).resolve()
            if path in seen:
                continue
            seen.add(path)
            manifest = read_json(path / "manifest.json")
            if "run_id" in manifest and "captures" in manifest:
                yield path


def grouping_keys(config: dict[str, Any], scene: dict[str, Any]) -> list[str]:
    """Group overlapping raw weather dates, not independent windows or station rows."""
    keys = [f"source:{scene['source_group_id']}"] if scene.get("source_group_id") else []
    if scene.get("pair_id"):
        keys.append(f"pair:{scene['pair_id']}")
    duration = config["protocol"]["duration_s"]
    for weather in [config["weather"], *config.get("weather_by_station", {}).values()]:
        start = datetime.fromisoformat(weather["source_start"].replace("Z", "+00:00"))
        last = start + timedelta(seconds=duration) - timedelta(microseconds=1)
        day = start.date()
        while day <= last.date():
            keys.append(f"weather:{weather['sha256']}:{day.isoformat()}")
            day += timedelta(days=1)
        # A reused high-frequency input template is a shared source. The current
        # grey optical *model* is an algorithm, not an external measured template.
        if weather.get("template_sha256"):
            keys.append(f"template:{weather['template_sha256']}")
    return sorted(set(keys))


def inspect_run(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json")
    record: dict[str, Any] = dict(
        run_id=manifest["run_id"],
        path=str(root),
        eligible=False,
        reasons=[],
        status=manifest.get("status"),
        code_version=manifest.get("code_version"),
        site_count=0,
        group_keys=[],
        pair_id=None,
        source_group_id=None,
    )
    reasons: list[str] = record["reasons"]
    config_path = root / "power_config.json"
    if not config_path.is_file():
        reasons.append("no_physical_configuration")
        return record
    config = read_json(config_path)
    stations = config["protocol"]["stations"]
    record["site_count"] = len(stations)
    if len(stations) not in (3, 10):
        reasons.append("historical_scale_excluded")
        return record
    if manifest.get("status") != "completed" or manifest.get("cleanup_errors"):
        reasons.append("incomplete_or_cleanup_failed")
    if manifest.get("code_dirty") is not False:
        reasons.append("unfrozen_simulation")
    if manifest.get("capture_topology") != "isolated_master_P0_and_RTU_P1":
        reasons.append("not_independent_isolated_endpoints")
    scene_path = root / "truth/scenario/effective.json"
    scene = read_json(scene_path) if scene_path.is_file() else {}
    record.update(
        group_keys=grouping_keys(config, scene),
        pair_id=scene.get("pair_id"),
        source_group_id=scene.get("source_group_id"),
        normal_scene=scene.get("normal_scene"),
        weather=config["weather"],
        duration_s=config["protocol"]["duration_s"],
        scene_clock=manifest.get("scene_clock"),
    )
    label_dir = root / "labels/v04-label-v3"
    qc_path = root / "qc/v04-label-qc-v4/report.json"
    if not qc_path.is_file() or not (label_dir / "manifest.json").is_file():
        reasons.append("current_labels_or_independent_qc_missing")
        return record
    qc = read_json(qc_path)
    metadata = read_json(label_dir / "manifest.json")
    label_path = label_dir / "event_labels.jsonl"
    labels = [EventLabel.model_validate(row) for row in iter_jsonl([label_path])]
    if not qc.get("passed") or qc.get("evaluation_code_dirty") is not False:
        reasons.append("independent_label_qc_failed")
    if qc.get("run_id") != manifest["run_id"] or metadata.get("run_id") != manifest["run_id"]:
        reasons.append("cross_run_identity")
    if (
        len(labels) != len(stations)
        or {label.station_id for label in labels} != {s["station_id"] for s in stations}
        or any(label.run_id != manifest["run_id"] for label in labels)
    ):
        reasons.append("label_identity_or_station_coverage")
    if any(label.label_status != "valid" or label.service_violation is None for label in labels):
        reasons.append("invalid_or_uncertain_labels")
    hashes = dict(metadata["source_hashes"])
    hashes.update(
        {
            "labels/v04-label-v3/event_labels.jsonl": metadata["label_sha256"],
            "labels/v04-label-v3/event_labels.parquet": metadata["parquet_sha256"],
        }
    )
    if qc.get("label_sha256") != metadata["label_sha256"] or qc.get(
        "label_manifest_sha256"
    ) != source_hash(label_dir / "manifest.json"):
        reasons.append("label_qc_hash_mismatch")
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or source_hash(path) != expected:
            reasons.append(f"evidence_hash_mismatch:{relative}")
    # A valid label auditor can correctly describe an invalid experiment. Require
    # its independent mechanism/data gate as well; never use label QC alone.
    mechanism_reports = [
        read_json(root / p)
        for p in metadata["source_hashes"]
        if p.startswith("qc/") and p.endswith("/report.json")
    ]
    if not mechanism_reports or not all(q.get("passed") for q in mechanism_reports):
        reasons.append("mechanism_or_normal_quality_failed")
    if not record["scene_clock"]:
        reasons.append("unified_scene_clock_missing")
    hashes["qc/v04-label-qc-v4/report.json"] = source_hash(qc_path)
    hashes["labels/v04-label-v3/manifest.json"] = source_hash(label_dir / "manifest.json")
    for name in ("packets", "iec104_apdu"):
        relative = f"parsed/tshark-pdml-v2/{name}.jsonl"
        if not (root / relative).is_file():
            reasons.append(f"missing_protocol_table:{name}")
        else:
            hashes[relative] = source_hash(root / relative)
    record.update(
        eligible=not reasons,
        evidence_sha256=hashes,
        fault_types=sorted({kind for label in labels for kind in label.fault_type}),
        violation_stations=sum(label.service_violation is True for label in labels),
        findings=sum(f.service_violation is True for label in labels for f in label.findings),
        retained_pair_reports=[
            dict(path=str(report), sha256=source_hash(report))
            for report in sorted((root.parent / "qc").glob("*/report.json"))
        ]
        if scene.get("pair_id")
        else [],
        label_path=str(label_path),
        files={
            "P0": str(root / "raw/pcap/primary.pcap"),
            "protocol": str(root / "parsed/tshark-pdml-v2"),
            "observed": str(root / "observed"),
            "qc": str(qc_path),
            "weather": config["weather"]["directory"],
        },
    )
    return record


def inventory_runs(roots: list[Path], destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(f"dataset directory exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalogue-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        rows: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        for root in sorted(run_directories(roots)):
            try:
                row = inspect_run(root)
            except (ValueError, KeyError, OSError) as exc:
                row = dict(path=str(root), eligible=False, reasons=[f"malformed:{exc}"])
            if row.get("eligible"):
                identity = row["run_id"]
                if identity in seen:
                    row.update(eligible=False, reasons=["duplicate_run_identity"])
                else:
                    seen[identity] = row
            rows.append(row)
        with (stage / "runs.jsonl").open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        report = dict(
            schema_version=VERSION,
            **code_record(),
            roots=[str(r.resolve()) for r in roots],
            discovered=len(rows),
            eligible=sum(r["eligible"] for r in rows),
            excluded_reasons=dict(Counter(reason for r in rows for reason in r["reasons"])),
            independent_runs_by_fault=dict(
                Counter(kind for row in rows if row["eligible"] for kind in row["fault_types"])
            ),
            run_index_sha256=source_hash(stage / "runs.jsonl"),
        )
        write_json(stage / "inventory.json", report)
        stage.rename(destination)
    return report


def split_records(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    parents = list(range(len(rows)))

    def find(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    keys: dict[str, int] = {}
    for i, row in enumerate(rows):
        for key in [f"run:{row['run_id']}", *row["group_keys"]]:
            if key in keys:
                parents[find(i)] = find(keys[key])
            keys[key] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(rows)):
        groups.setdefault(find(i), []).append(i)
    ordered = sorted(groups.values(), key=lambda g: sorted(rows[i]["run_id"] for i in g))
    random.Random(seed).shuffle(ordered)
    if len(ordered) < 3:
        raise ValueError(
            "fewer than three independent source/date/pair groups; add independent dates"
        )
    train_n = min(len(ordered) - 2, max(1, math.floor(len(ordered) * 0.6)))
    validation_n = min(len(ordered) - train_n - 1, max(1, math.floor(len(ordered) * 0.2)))
    result: list[dict[str, Any]] = []
    for rank, group in enumerate(ordered):
        partition = (
            "train"
            if rank < train_n
            else ("validation" if rank < train_n + validation_n else "test")
        )
        group_id = hashlib.sha256(
            "\n".join(sorted({key for i in group for key in rows[i]["group_keys"]})).encode()
        ).hexdigest()
        result.extend(
            dict(
                run_id=rows[i]["run_id"],
                partition=partition,
                group_id=group_id,
                group_keys=rows[i]["group_keys"],
                path=rows[i]["path"],
            )
            for i in group
        )
    return sorted(result, key=lambda r: r["run_id"])


def split_dataset(root: Path, policy: DatasetPolicy) -> dict[str, Any]:
    destination = root / "split"
    if destination.exists():
        raise FileExistsError("split already frozen")
    catalogue = read_json(root / "inventory.json")
    if source_hash(root / "runs.jsonl") != catalogue["run_index_sha256"]:
        raise ValueError("run index changed after inventory")
    rows = [r for r in iter_jsonl([root / "runs.jsonl"]) if r["eligible"]]
    profile: dict[str, Any] | None = None
    if policy.training_protocol == "S1_stratified_development":
        from pvcomm.pipeline.coverage import profile_records

        profile = profile_records(rows)
        assignment, stratification = stratified_records(rows, profile, policy.split_seed)
    else:
        assignment = split_records(rows, policy.split_seed)
        stratification = None
    partitions: dict[str, set[str]] = {}
    for row in assignment:
        for key in [f"run:{row['run_id']}", *row["group_keys"]]:
            partitions.setdefault(key, set()).add(row["partition"])
    crossed = [k for k, parts in partitions.items() if len(parts) != 1]
    if crossed:
        raise ValueError("source/date/pair leakage")
    with tempfile.TemporaryDirectory(prefix=".split-", dir=root) as temporary:
        stage = Path(temporary) / "split"
        stage.mkdir()
        write_json(stage / "assignment.json", dict(runs=assignment))
        write_json(stage / "policy.json", policy.model_dump(mode="json"))
        if profile is not None:
            write_json(stage / "source_profile.json", profile)
            write_json(stage / "stratification.json", stratification)
        report = dict(
            passed=True,
            schema_version=policy.schema_version,
            grouped_before_windowing=True,
            crossed_keys=crossed,
            partition_runs=dict(Counter(r["partition"] for r in assignment)),
            independent_groups=len({r["group_id"] for r in assignment}),
            assignment_sha256=source_hash(stage / "assignment.json"),
            inventory_sha256=source_hash(root / "inventory.json"),
            policy_sha256=source_hash(stage / "policy.json"),
            profile_sha256=source_hash(stage / "source_profile.json")
            if profile is not None
            else None,
            stratification_sha256=source_hash(stage / "stratification.json")
            if profile is not None
            else None,
            **code_record(),
        )
        write_json(stage / "audit.json", report)
        stage.rename(destination)
    return report


def stratified_records(
    rows: list[dict[str, Any]], profile: dict[str, Any], seed: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exact small-group search, with registered lexicographic evidence objectives."""
    legacy = split_records(rows, seed)
    group_ids = sorted({a["group_id"] for a in legacy})
    if len(group_ids) > 12:
        raise ValueError("registered exact split supports at most 12 source groups")
    lookup = {a["run_id"]: a["group_id"] for a in legacy}
    scales = ("W10", "W30", "W60")
    groups = []
    for group_id in group_ids:
        all_members = [r for r in profile["runs"] if lookup[r["run_id"]] == group_id]
        members = [r for r in all_members if r["site_count"] == 3]
        groups.append(
            dict(
                group_id=group_id,
                runs=len(members),
                supplemental_runs=len(all_members) - len(members),
                group_keys=sorted({k for r in all_members for k in r["group_keys"]}),
                normal_station_hours=sum(r["normal_station_hours"] for r in members),
                normal_scenes=sorted({r["normal_scene"] for r in members if r["normal_scene"]}),
                fault_types=sorted({k for r in members for k in r["fault_types"]}),
                raw_events=sum(r["events"] for r in members),
                scales={
                    s: dict(
                        events=sum(r["scales"][s]["evaluable_events"] for r in members),
                        event_runs=sum(r["scales"][s]["evaluable_events"] > 0 for r in members),
                        positives=sum(r["scales"][s]["positives"] for r in members),
                        normal=sum(r["scales"][s]["normal"] for r in members),
                    )
                    for s in scales
                },
            )
        )
    totals = [sum(g["scales"][s]["event_runs"] for g in groups) for s in scales]
    hours = sum(g["normal_station_hours"] for g in groups)
    best: tuple[Any, ...] | None = None
    chosen: tuple[int, ...] = ()
    evaluated = 0
    for allocation in itertools.product(range(3), repeat=len(groups)):
        if len(set(allocation)) != 3:
            continue
        counts = [[0] * 3 for _ in range(3)]
        normal = [[0] * 3 for _ in range(3)]
        durations = [0.0] * 3
        kinds: list[set[str]] = [set(), set(), set()]
        scenes: list[set[str]] = [set(), set(), set()]
        for group, part in zip(groups, allocation, strict=True):
            durations[part] += group["normal_station_hours"]
            kinds[part].update(group["fault_types"])
            scenes[part].update(group["normal_scenes"])
            for j, scale in enumerate(scales):
                counts[part][j] += group["scales"][scale]["event_runs"]
                normal[part][j] += group["scales"][scale]["normal"]
        missing = sum(counts[p][j] == 0 for p in range(3) for j in range(3))
        missing += sum(normal[p][j] == 0 for p in range(3) for j in range(3))
        weakest = min(counts[p][j] / max(1, totals[j]) for p in range(3) for j in range(3))
        coverage = sum(len(x) for x in kinds) + sum(len(x) for x in scenes)
        balance = sum(
            abs(counts[p][j] / max(1, totals[j]) - ratio)
            for p, ratio in enumerate((0.6, 0.2, 0.2))
            for j in range(3)
        ) + sum(
            abs(durations[p] / max(hours, 1e-12) - ratio) for p, ratio in enumerate((0.6, 0.2, 0.2))
        )
        tie = hashlib.sha256(f"{seed}:{allocation}".encode()).hexdigest()
        objective = (missing, -weakest, -coverage, round(balance, 12), tie)
        evaluated += 1
        if best is None or objective < best:
            best, chosen = objective, allocation
    parts = ("train", "validation", "test")
    frozen = dict(zip(group_ids, (parts[p] for p in chosen), strict=True))
    for row in legacy:
        row["partition"] = frozen[row["group_id"]]
    return legacy, dict(
        groups=groups,
        group_partitions=frozen,
        objective=best,
        candidates=evaluated,
        seed=seed,
        primary_site_count=3,
        supplemental_site_count=10,
        strategy="lexicographic missing cells, weakest independent event-run coverage, "
        "mechanism/context coverage, 60/20/20 balance, seeded SHA tie",
        model_performance_used=False,
        evaluation_use="development_only",
    )
