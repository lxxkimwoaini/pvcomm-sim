"""Independent export checks: group isolation, whitelist, causal bounds and labels."""

from __future__ import annotations

import importlib
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.domain.labels import EventLabel
from pvcomm.pipeline.dataset import code_record, read_json, write_json
from pvcomm.pipeline.features import FEATURES
from pvcomm.storage.parquet import iter_jsonl, source_hash


def qc_dataset(root: Path, scale: str | None = None) -> dict[str, Any]:
    output_root = root / scale if scale else root
    destination = output_root / "leakage_report.json"
    if destination.exists():
        raise FileExistsError("dataset QC already exists")
    folder = output_root / "features-M2"
    policy = DatasetPolicy.model_validate(read_json(root / "split/policy.json"))
    if policy.schema_version == "0.5.1-dataset.1" and scale is None:
        raise ValueError("V0.5.1 requires an explicit registered scale")
    if scale:
        policy = policy.for_scale(scale)
    inventory = read_json(root / "inventory.json")
    split_audit = read_json(root / "split/audit.json")
    split = read_json(root / "split/assignment.json")["runs"]
    feature_quality = read_json(folder / "quality.json")
    hashes = source_hash(root / "runs.jsonl") == inventory["run_index_sha256"]
    hashes &= source_hash(root / "inventory.json") == split_audit["inventory_sha256"]
    hashes &= source_hash(root / "split/assignment.json") == split_audit["assignment_sha256"]
    hashes &= feature_quality["split_sha256"] == split_audit["assignment_sha256"]
    for name, digest in feature_quality["tables"].items():
        hashes &= source_hash(folder / f"{name}.parquet") == digest
    partitions: dict[str, set[str]] = defaultdict(set)
    for row in split:
        for key in [f"run:{row['run_id']}", *row["group_keys"]]:
            partitions[key].add(row["partition"])
    group_isolation = all(len(parts) == 1 for parts in partitions.values())
    assignments = {r["run_id"]: r for r in split}
    catalogue = {r["run_id"]: r for r in iter_jsonl([root / "runs.jsonl"]) if r["eligible"]}
    pq = importlib.import_module("pyarrow.parquet")
    features = pq.read_table(folder / "features.parquet")
    whitelist = features.column_names == ["sample_id", *FEATURES["M2"]]
    whitelist &= features.schema.metadata.get(b"scope") == b"M2"
    labels = pq.read_table(folder / "labels.parquet").to_pylist()
    indices = pq.read_table(folder / "index.parquet").to_pylist()
    samples = features.column("sample_id").to_pylist()
    alignment = samples == [r["sample_id"] for r in labels] == [r["sample_id"] for r in indices]
    alignment &= len(samples) == len(set(samples)) == feature_quality["rows"]
    finite = all(
        value is None or math.isfinite(value)
        for name in FEATURES["M2"]
        for value in features.column(name).to_pylist()
    )
    truth: dict[tuple[str, str], EventLabel] = {}
    for identity, record in catalogue.items():
        for raw in iter_jsonl([Path(record["label_path"])]):
            truth[identity, raw["station_id"]] = EventLabel.model_validate(raw)
    causal = True
    label_matches = True
    group_rows = True
    counts: Counter[str] = Counter()
    positive_counts: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    for index, label in zip(indices, labels, strict=True):
        assignment = assignments[index["run_id"]]
        group_rows &= (index["partition"], index["group_id"]) == (
            assignment["partition"],
            assignment["group_id"],
        )
        begin, end = index["window_start_utc_ns"], index["window_end_utc_ns"]
        record = catalogue[index["run_id"]]
        causal &= index["latest_input_available_at"] is None or (
            index["latest_input_available_at"] < end
        )
        causal &= end - begin == policy.window_s * 10**9
        causal &= begin >= record["scene_clock"]["start_utc_ns"]
        source = truth[index["run_id"], index["station_id"]]
        # Independent endpoint/coverage computation. Labels cannot simply follow
        # the run fault flag or the proportion of abnormal rows in this run.
        intervals = []
        for finding in source.findings:
            if finding.service_violation is True and finding.label_status == "valid":
                left = finding.start_utc_ns
                right = finding.end_utc_ns if finding.end_utc_ns is not None else end
                if left < end and right > begin:
                    intervals.append((max(begin, left), min(end, right)))
        expected_end = int(any(left <= end - 1 < right for left, right in intervals))
        points = sorted({begin, end, *(x for pair in intervals for x in pair)})
        covered = sum(
            b - a
            for a, b in zip(points, points[1:], strict=False)
            if any(left <= a and b <= right for left, right in intervals)
        )
        label_matches &= label["label_status"] == "valid"
        label_matches &= label["label_at_end"] == expected_end
        label_matches &= label["label_any"] == int(bool(intervals))
        label_matches &= label["observable_at_end"] == int(
            any(
                finding.service_violation is True
                and finding.label_status == "valid"
                and max(finding.start_utc_ns, finding.observable_at_utc_ns or finding.start_utc_ns)
                <= end - 1
                < (finding.end_utc_ns if finding.end_utc_ns is not None else end)
                for finding in source.findings
            )
        )
        label_matches &= abs(label["anomaly_overlap_ratio"] - covered / (end - begin)) < 1e-12
        counts[index["partition"]] += 1
        positive_counts[index["partition"]] += expected_end
        endpoint_counts[f"{index['partition']}:{index['station_id']}"] += 1
    checks = dict(
        immutable_sources_and_tables=bool(hashes),
        no_source_date_run_pair_crossing=group_isolation,
        feature_whitelist=bool(whitelist),
        unique_aligned_samples=bool(alignment),
        finite_or_missing_features=finite,
        causal_window_bounds=bool(causal),
        independent_window_label_mapping=bool(label_matches),
        split_rows_match=bool(group_rows),
    )
    evidence_qc = None
    event_coverage = None
    if scale:
        from pvcomm.pipeline.coverage import classify_event, run_bounds, service_events
        from pvcomm.qc.feature_evidence import check_feature_evidence

        events = service_events(catalogue)
        for name, key in (
            ("source_profile", "profile_sha256"),
            ("stratification", "stratification_sha256"),
            ("policy", "policy_sha256"),
        ):
            hashes &= source_hash(root / f"split/{name}.json") == split_audit[key]
        evidence_qc = check_feature_evidence(catalogue, indices, features.to_pylist(), events)
        checks["fixed_sample_independent_feature_recalculation"] = evidence_qc["passed"]
        endpoints: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index in indices:
            endpoints[index["run_id"], index["station_id"]].append(index["window_end_utc_ns"])
        grid_matches = True
        for run_id, record in catalogue.items():
            _, warm, end_run = run_bounds(record)
            expected = list(
                range(warm + policy.window_s * 10**9, end_run + 1, policy.step_s * 10**9)
            )
            for station in truth:
                if station[0] == run_id:
                    grid_matches &= sorted(endpoints[station]) == expected
            for relative, digest in record["evidence_sha256"].items():
                hashes &= source_hash(Path(record["path"]) / relative) == digest
        checks["immutable_sources_and_tables"] = bool(hashes)
        checks["registered_sampling_grid"] = grid_matches
        raw_partitions: dict[str, set[str]] = defaultdict(set)
        for run_id, record in catalogue.items():
            p0 = record.get("files", {}).get("P0")
            if p0:
                raw_partitions[source_hash(Path(p0))].add(assignments[run_id]["partition"])
        checks["no_duplicate_raw_capture_crossing"] = all(
            len(p) == 1 for p in raw_partitions.values()
        )
        checks["primary_partition_class_support"] = all(
            {
                y["label_at_end"]
                for r, y in zip(indices, labels, strict=True)
                if r["partition"] == part and catalogue[r["run_id"]]["site_count"] == 3
            }
            == {0, 1}
            for part in ("train", "validation", "test")
        )
        profile = read_json(root / "split/source_profile.json")
        group_lookup = {r["run_id"]: r["group_id"] for r in split}
        event_coverage = []
        for event in events:
            partition = assignments[event["run_id"]]["partition"]
            times = endpoints[event["run_id"], event["station_id"]]
            event_coverage.append(
                dict(
                    event,
                    partition=partition,
                    site_count=catalogue[event["run_id"]]["site_count"],
                    group_id=group_lookup[event["run_id"]],
                    evaluation_status=classify_event(
                        event, times, event["warmup_end_utc_ns"] + policy.window_s * 10**9
                    ),
                )
            )
        independent_by_run = Counter(
            e["run_id"] for e in event_coverage if e["evaluation_status"] == "evaluable"
        )
        checks["preregistered_profile_event_counts"] = all(
            independent_by_run[r["run_id"]] == r["scales"][scale]["evaluable_events"]
            for r in profile["runs"]
        )
        write_json(output_root / "feature_evidence_qc.json", evidence_qc)
        write_json(
            output_root / "event_coverage.json",
            dict(
                events=event_coverage,
                primary_site_count=3,
                by_partition={
                    part: dict(
                        raw_events=len(
                            es := [
                                e
                                for e in event_coverage
                                if e["partition"] == part and e["site_count"] == 3
                            ]
                        ),
                        independent_event_runs=len({e["run_id"] for e in es}),
                        evaluable_events=sum(e["evaluation_status"] == "evaluable" for e in es),
                        evaluable_event_runs=len(
                            {e["run_id"] for e in es if e["evaluation_status"] == "evaluable"}
                        ),
                        statuses=dict(Counter(e["evaluation_status"] for e in es)),
                        fault_event_counts=dict(Counter(k for e in es for k in e["fault_types"])),
                        evaluable_fault_event_counts=dict(
                            Counter(
                                k
                                for e in es
                                if e["evaluation_status"] == "evaluable"
                                for k in e["fault_types"]
                            )
                        ),
                    )
                    for part in ("train", "validation", "test")
                },
            ),
        )
    report = dict(
        passed=all(checks.values()),
        checks=checks,
        checked_samples=len(samples),
        schema_version="0.5.1-dataset-qc.1" if scale else "0.5-dataset-qc.1",
        scale=scale,
        crossed_groups=0 if group_isolation else None,
        oracle_columns=[],
        feature_quality_sha256=source_hash(folder / "quality.json"),
        run_index_sha256=source_hash(root / "runs.jsonl"),
        split_sha256=source_hash(root / "split/assignment.json"),
        **code_record(),
    )
    statistics = dict(
        eligible_runs=len(catalogue),
        site_counts=dict(Counter(str(r["site_count"]) for r in catalogue.values())),
        independent_groups=split_audit["independent_groups"],
        windows_by_split=dict(counts),
        positives_at_end_by_split=dict(positive_counts),
        normal_at_end_by_split={p: count - positive_counts[p] for p, count in counts.items()},
        endpoints_by_station_partition=dict(endpoint_counts),
        fault_runs_by_partition={
            part: dict(
                Counter(
                    kind
                    for row in split
                    if row["partition"] == part
                    for kind in catalogue[row["run_id"]]["fault_types"]
                )
            )
            for part in ("train", "validation", "test")
        },
        source_dates=sorted({r["weather"]["source_start"][:10] for r in catalogue.values()}),
        actual_run_hours=sum(r["duration_s"] for r in catalogue.values()) / 3600,
        duration_note="independent initialized segments; not one continuous time series",
        window_s=policy.window_s,
        step_s=policy.step_s,
        overlap_ratio=1 - policy.step_s / policy.window_s,
        primary_by_partition={
            part: dict(
                windows=sum(
                    r["partition"] == part and catalogue[r["run_id"]]["site_count"] == 3
                    for r in indices
                ),
                positives=sum(
                    y["label_at_end"] == 1
                    for r, y in zip(indices, labels, strict=True)
                    if r["partition"] == part and catalogue[r["run_id"]]["site_count"] == 3
                ),
            )
            for part in ("train", "validation", "test")
        },
        normal_context_runs_by_partition={
            part: dict(
                Counter(
                    str(catalogue[r["run_id"]].get("normal_scene"))
                    for r in split
                    if r["partition"] == part and catalogue[r["run_id"]]["site_count"] == 3
                )
            )
            for part in ("train", "validation", "test")
        },
        known_anomalous_runs_without_endpoint_violation=[
            r["run_id"]
            for r in catalogue.values()
            if r["fault_types"] and r["violation_stations"] == 0
        ],
    )
    write_json(output_root / "dataset_statistics.json", statistics)
    write_json(destination, report)
    return report
