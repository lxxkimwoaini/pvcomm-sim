"""Small prerelease baselines with frozen groups and train-only preprocessing."""

from __future__ import annotations

import importlib
import math
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.domain.labels import EventLabel
from pvcomm.pipeline.dataset import code_record, read_json, write_json
from pvcomm.pipeline.features import FEATURES
from pvcomm.storage.parquet import iter_jsonl, source_hash


def choose_threshold(labels: Any, scores: Any, *, legacy: bool = False) -> float:
    """Validation F1, conservative tie break; never inspect test labels here."""
    np = importlib.import_module("numpy")
    metrics = importlib.import_module("sklearn.metrics")
    if len(set(labels.tolist())) < 2:
        # With a single validation class report the limitation and use its
        # normal-score 99th percentile (fixed engineering selection rule).
        if legacy:
            return float(np.quantile(scores, 0.99))
        if len(scores) == 0 or not np.any(labels == 0):
            raise ValueError("threshold calibration requires normal validation observations")
        # >= is fixed. Include the representable value just above the maximum
        # so tied discrete scores can satisfy the preregistered <=1% FPR.
        candidates = [*np.unique(scores), np.nextafter(np.max(scores), np.inf)]
        return min(float(t) for t in candidates if float(np.mean(scores >= t)) <= 0.01)
    precision, recall, thresholds = metrics.precision_recall_curve(labels, scores)
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-15)
    candidates = thresholds[f1 == np.max(f1)]
    return float(np.max(candidates))


def alert_episodes(
    indices: list[dict[str, Any]], predictions: list[int], merge_s: int
) -> list[tuple[str, str, list[int]]]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, predicted in zip(indices, predictions, strict=True):
        if predicted:
            grouped[index["run_id"], index["station_id"]].append(index["window_end_utc_ns"])
    episodes = []
    for (run, station), times in sorted(grouped.items()):
        current: list[int] = []
        for timestamp in sorted(times):
            if current and timestamp - current[-1] > merge_s * 10**9:
                episodes.append((run, station, current))
                current = []
            current.append(timestamp)
        if current:
            episodes.append((run, station, current))
    return episodes


def service_events(catalogue: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for identity, record in catalogue.items():
        root = Path(record["path"])
        end_run = record["scene_clock"]["start_utc_ns"] + record["duration_s"] * 10**9
        for raw in iter_jsonl([root / "labels/v04-label-v3/event_labels.jsonl"]):
            label = EventLabel.model_validate(raw)
            intervals = sorted(
                (
                    max(f.start_utc_ns, f.observable_at_utc_ns or f.start_utc_ns),
                    f.end_utc_ns if f.end_utc_ns is not None else end_run,
                )
                for f in label.findings
                if f.service_violation is True and f.label_status == "valid"
            )
            merged: list[list[int]] = []
            for left, right in intervals:
                if merged and left <= merged[-1][1]:
                    merged[-1][1] = max(right, merged[-1][1])
                else:
                    merged.append([left, right])
            events.extend(
                dict(
                    run_id=identity,
                    station_id=label.station_id,
                    start_utc_ns=left,
                    end_utc_ns=right,
                )
                for left, right in merged
            )
    return events


def evaluate(
    labels: Any,
    scores: Any,
    threshold: float,
    indices: list[dict[str, Any]],
    events: list[dict[str, Any]],
    policy: DatasetPolicy,
) -> dict[str, Any]:
    np = importlib.import_module("numpy")
    metrics = importlib.import_module("sklearn.metrics")
    predictions = (scores >= threshold).astype(int)
    normal = labels == 0
    false_positives = int(np.sum(predictions[normal]))
    episodes = alert_episodes(indices, predictions.tolist(), policy.alert_merge_s)
    if policy.schema_version == "0.5.1-dataset.1":
        from pvcomm.experiments.event_metrics import evaluate_events

        precision, recall, f1, _ = metrics.precision_recall_fscore_support(
            labels, predictions, average="binary", zero_division=0
        )
        return dict(
            windows=len(labels),
            positives=int(np.sum(labels)),
            threshold=threshold,
            precision=float(precision),
            recall=float(recall),
            f1=float(f1),
            pr_auc_average_precision=float(metrics.average_precision_score(labels, scores))
            if len(set(labels.tolist())) == 2
            else None,
            normal_false_positive_rate=false_positives / int(np.sum(normal))
            if np.any(normal)
            else None,
            single_class_test=len(set(labels.tolist())) < 2,
            **evaluate_events(
                indices, predictions.tolist(), labels.tolist(), events, episodes, policy
            ),
        )
    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in indices:
        observed[row["run_id"], row["station_id"]].append(row["window_end_utc_ns"])
    eligible_events = [
        e
        for e in events
        if (times := observed.get((e["run_id"], e["station_id"])))
        and e["start_utc_ns"] < max(times)
        and min(times) <= e["end_utc_ns"]
    ]
    used: set[int] = set()
    outcomes = []
    for event in sorted(eligible_events, key=lambda e: e["start_utc_ns"]):
        candidates = []
        for i, (run, station, times) in enumerate(episodes):
            if i in used or (run, station) != (event["run_id"], event["station_id"]):
                continue
            hits = [t for t in times if event["start_utc_ns"] < t <= event["end_utc_ns"]]
            if hits:
                candidates.append((min(hits), i))
        detected = bool(candidates)
        delay = None
        if detected:
            first, i = min(candidates)
            used.add(i)
            delay = (first - event["start_utc_ns"]) / 1e9
        outcomes.append(dict(event, detected=detected, delay_s=delay, censored=not detected))
    # False episodes count only if every alert endpoint has a normal endpoint
    # label. One episode cannot match multiple service events.
    endpoint_labels = {
        (r["run_id"], r["station_id"], r["window_end_utc_ns"]): int(y)
        for r, y in zip(indices, labels, strict=True)
    }
    false_episodes = sum(
        all(endpoint_labels[run, station, t] == 0 for t in times)
        for run, station, times in episodes
    )
    normal_hours = int(np.sum(normal)) * policy.step_s / 3600
    delays = [r["delay_s"] for r in outcomes if r["detected"]]
    precision, recall, f1, _ = metrics.precision_recall_fscore_support(
        labels, predictions, average="binary", zero_division=0
    )
    return dict(
        windows=len(labels),
        positives=int(np.sum(labels)),
        threshold=threshold,
        precision=float(precision),
        recall=float(recall),
        f1=float(f1),
        pr_auc_average_precision=float(metrics.average_precision_score(labels, scores))
        if len(set(labels.tolist())) == 2
        else None,
        normal_false_positive_rate=false_positives / int(np.sum(normal))
        if np.any(normal)
        else None,
        normal_endpoint_exposure_station_hours=normal_hours,
        false_alert_episodes=false_episodes,
        false_alerts_per_station_hour=false_episodes / normal_hours if normal_hours else None,
        event_count=len(outcomes),
        detected_events=len(delays),
        missed_events=len(outcomes) - len(delays),
        event_recall=len(delays) / len(outcomes) if outcomes else None,
        detection_delay_mean_s=statistics.mean(delays) if delays else None,
        detection_delay_p95_s=float(np.quantile(delays, 0.95)) if delays else None,
        event_outcomes=outcomes,
        single_class_test=len(set(labels.tolist())) < 2,
    )


def temporal_scores(values: Any, indices: list[dict[str, Any]]) -> Any:
    """Fixed EWMA of train-standardized values; reset state per run and station."""
    np = importlib.import_module("numpy")
    scores = np.zeros(len(values))
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(indices):
        grouped[row["run_id"], row["station_id"]].append(i)
    for members in grouped.values():
        state = np.zeros(values.shape[1])
        for i in sorted(members, key=lambda j: indices[j]["window_end_utc_ns"]):
            state = 0.3 * values[i] + 0.7 * state
            scores[i] = np.max(np.abs(state))
    return scores


RULE_PROXIES = (
    "power_age_budget_ratio",
    "confirmation_wait_s",
    "execution_wait_s",
    "fresh_point_coverage",
    "known_maintenance_active",
    "maintenance_recovery_remaining_s",
    "power_age_s",
    "available_age_s",
    "command_age_s",
)
PEER_FEATURES = ("peer_fresh_power_coverage", "peer_mean_power_kw", "peer_mean_power_age_s")


def benchmark_dataset(
    root: Path, mode: str = "M2", scale: str | None = None, ablation: str | None = None
) -> dict[str, Any]:
    output_root = root / scale if scale else root
    destination = output_root / f"benchmark-{mode}{'-' + ablation if ablation else ''}"
    if destination.exists():
        raise FileExistsError("benchmark results already exist")
    if mode not in FEATURES:
        raise ValueError("only M0/M1/M2 observations are implemented")
    # All comparisons project the same independently audited sample table.
    folder = output_root / "features-M2"
    quality = read_json(folder / "quality.json")
    if not quality["passed"]:
        raise ValueError("feature quality failed")
    independent_qc = read_json(output_root / "leakage_report.json")
    if (
        not independent_qc["passed"]
        or independent_qc["feature_quality_sha256"] != source_hash(folder / "quality.json")
        or independent_qc["run_index_sha256"] != source_hash(root / "runs.jsonl")
        or independent_qc["split_sha256"] != source_hash(root / "split/assignment.json")
    ):
        raise ValueError("independent dataset QC missing, failed or stale")
    for name, digest in quality["tables"].items():
        if source_hash(folder / f"{name}.parquet") != digest:
            raise ValueError("training table changed after export")
    np = importlib.import_module("numpy")
    pq = importlib.import_module("pyarrow.parquet")
    ensemble = importlib.import_module("sklearn.ensemble")
    preprocessing = importlib.import_module("sklearn.preprocessing")
    impute = importlib.import_module("sklearn.impute")
    policy = DatasetPolicy.model_validate(read_json(root / "split/policy.json"))
    if policy.schema_version == "0.5.1-dataset.1" and scale is None:
        raise ValueError("V0.5.1 requires an explicit registered scale")
    if scale:
        policy = policy.for_scale(scale)
    if ablation not in (None, "without-rule-proxies", "without-peer") or (
        ablation and mode != "M2"
    ):
        raise ValueError("registered ablations apply only to M2")
    removed = (
        RULE_PROXIES
        if ablation == "without-rule-proxies"
        else (PEER_FEATURES if ablation == "without-peer" else ())
    )
    columns = [name for name in FEATURES[mode] if name not in removed]
    frames = {
        name: pq.read_table(folder / f"{name}.parquet").to_pylist()
        for name in ("features", "labels", "rules", "index")
    }
    ids = [r["sample_id"] for r in frames["index"]]
    if len(ids) != len(set(ids)) or any(
        [r["sample_id"] for r in values] != ids for values in frames.values()
    ):
        raise ValueError("duplicate or misaligned training samples")
    catalogue = {r["run_id"]: r for r in iter_jsonl([root / "runs.jsonl"]) if r["eligible"]}
    known = [
        i
        for i, r in enumerate(frames["labels"])
        if r["label_status"] == "valid"
        and r["label_at_end"] in (0, 1)
        and (scale is None or catalogue[frames["index"][i]["run_id"]]["site_count"] == 3)
    ]
    indices = [frames["index"][i] for i in known]
    labels = np.array([frames["labels"][i]["label_at_end"] for i in known])
    x = np.array([[frames["features"][i][name] for name in columns] for i in known], dtype=float)
    masks = {
        part: np.array([r["partition"] == part for r in indices])
        for part in ("train", "validation", "test")
    }
    if any(not np.any(mask) for mask in masks.values()):
        raise ValueError("all three independent partitions require valid windows")
    normal_train = masks["train"] & (labels == 0)
    if int(np.sum(normal_train)) < policy.minimum_train_normal:
        raise ValueError("insufficient confirmed normal training windows")
    if scale:
        from pvcomm.pipeline.coverage import service_events as sampled_events

        test_runs = {
            r["run_id"]
            for r in read_json(root / "split/assignment.json")["runs"]
            if r["partition"] == "test" and catalogue[r["run_id"]]["site_count"] == 3
        }
        events = sampled_events({k: v for k, v in catalogue.items() if k in test_runs})
    else:
        events = service_events(catalogue)
    test_indices = [r for r, select in zip(indices, masks["test"], strict=True) if select]
    results: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    train_indices = [r for r, select in zip(indices, masks["train"], strict=True) if select]
    supplemental = [
        i
        for i, r in enumerate(frames["index"])
        if scale
        and catalogue[r["run_id"]]["site_count"] == 10
        and frames["labels"][i]["label_status"] == "valid"
    ]
    supplemental_indices = [frames["index"][i] for i in supplemental]
    supplemental_events = (
        sampled_events({k: v for k, v in catalogue.items() if v["site_count"] == 10})
        if scale
        else []
    )
    supplemental_results = []
    for algorithm in ("business_rule", "isolation_forest", "random_forest", "ewma"):
        seeds = policy.model_seeds if algorithm in ("isolation_forest", "random_forest") else (0,)
        for seed in seeds:
            fit_mask = masks["train"] if algorithm == "random_forest" else normal_train
            if algorithm == "random_forest" and len(set(labels[fit_mask].tolist())) < 2:
                results.append(
                    dict(
                        algorithm=algorithm,
                        seed=seed,
                        status="blocked",
                        reason="single_class_training_partition",
                    )
                )
                continue
            if algorithm == "business_rule":
                scores = np.array([frames["rules"][i]["business_rule"] for i in known], dtype=float)
                threshold = 0.5
                preprocessing_record: dict[str, Any] = {}
            else:
                filler = impute.SimpleImputer(
                    strategy="median", add_indicator=True, keep_empty_features=True
                )
                filler.fit(x[fit_mask])
                scaler = preprocessing.StandardScaler().fit(filler.transform(x[fit_mask]))
                transformed = scaler.transform(filler.transform(x))
                if algorithm == "isolation_forest":
                    model = ensemble.IsolationForest(
                        n_estimators=100, max_samples=256, random_state=seed, n_jobs=1
                    )
                    model.fit(transformed[fit_mask])
                    scores = -model.decision_function(transformed)
                elif algorithm == "random_forest":
                    model = ensemble.RandomForestClassifier(
                        n_estimators=100,
                        max_depth=6,
                        min_samples_leaf=2,
                        class_weight="balanced",
                        random_state=seed,
                        n_jobs=1,
                    )
                    model.fit(transformed[fit_mask], labels[fit_mask])
                    scores = model.predict_proba(transformed)[:, list(model.classes_).index(1)]
                else:
                    scores = temporal_scores(transformed, indices)
                threshold = choose_threshold(
                    labels[masks["validation"]],
                    scores[masks["validation"]],
                    legacy=policy.schema_version == "0.5-dataset.1",
                )
                preprocessing_record = dict(
                    fit_partition="train",
                    fit_normal_only=algorithm != "random_forest",
                    fitted_rows=int(np.sum(fit_mask)),
                    imputation_statistics=[
                        float(v) if math.isfinite(v) else None for v in filler.statistics_
                    ],
                    standardization_mean=scaler.mean_.tolist(),
                    standardization_scale=scaler.scale_.tolist(),
                )
            result = evaluate(
                labels[masks["test"]],
                scores[masks["test"]],
                threshold,
                test_indices,
                events,
                policy,
            )
            result.update(
                algorithm=algorithm,
                observation_mode="M2" if algorithm == "business_rule" else mode,
                seed=seed,
                status="completed",
                preprocessing=preprocessing_record,
                validation_single_class=len(set(labels[masks["validation"]].tolist())) < 2,
                validation_false_positive_rate=float(
                    np.mean(scores[masks["validation"] & (labels == 0)] >= threshold)
                )
                if np.any(masks["validation"] & (labels == 0))
                else None,
                threshold_method="fixed_rule"
                if algorithm == "business_rule"
                else (
                    "validation_F1_highest_threshold_tie"
                    if len(set(labels[masks["validation"]].tolist())) == 2
                    else "normal_validation_FPR_le_0.01_discrete_ge"
                ),
            )
            # Scenario metadata is used only for an offline false-positive report.
            contexts: dict[str, list[int]] = defaultdict(list)
            test_labels = labels[masks["test"]]
            test_scores = scores[masks["test"]]
            for i, row in enumerate(test_indices):
                if test_labels[i] == 0:
                    contexts[str(catalogue[row["run_id"]].get("normal_scene"))].append(i)
            result["normal_context_false_positive_rate"] = {
                context: float(np.mean(test_scores[members] >= threshold))
                for context, members in contexts.items()
            }
            if supplemental:
                if algorithm == "business_rule":
                    extra_scores = np.array(
                        [frames["rules"][i]["business_rule"] for i in supplemental]
                    )
                else:
                    extra_x = np.array(
                        [[frames["features"][i][name] for name in columns] for i in supplemental],
                        dtype=float,
                    )
                    extra_x = scaler.transform(filler.transform(extra_x))
                    if algorithm == "isolation_forest":
                        extra_scores = -model.decision_function(extra_x)
                    elif algorithm == "random_forest":
                        extra_scores = model.predict_proba(extra_x)[
                            :, list(model.classes_).index(1)
                        ]
                    else:
                        extra_scores = temporal_scores(extra_x, supplemental_indices)
                extra_result = evaluate(
                    np.array([frames["labels"][i]["label_at_end"] for i in supplemental]),
                    extra_scores,
                    threshold,
                    supplemental_indices,
                    supplemental_events,
                    policy,
                )
                supplemental_results.append(
                    dict(
                        extra_result,
                        algorithm=algorithm,
                        seed=seed,
                        evaluation_use="shared_source_diagnostic_only",
                    )
                )
                prediction_rows.extend(
                    dict(
                        sample_id=row["sample_id"],
                        algorithm=algorithm,
                        seed=seed,
                        score=float(score),
                        predicted=int(score >= threshold),
                        evaluation_set="ten_site_diagnostic",
                    )
                    for row, score in zip(supplemental_indices, extra_scores, strict=True)
                )
            results.append(result)
            prediction_rows.extend(
                dict(
                    sample_id=row["sample_id"],
                    algorithm=algorithm,
                    seed=seed,
                    score=float(score),
                    predicted=int(score >= threshold),
                    evaluation_set="primary_three_site_test",
                )
                for row, score in zip(test_indices, test_scores, strict=True)
            )
    summaries = {}
    for algorithm in ("business_rule", "isolation_forest", "random_forest", "ewma"):
        members = [r for r in results if r["algorithm"] == algorithm and r["status"] == "completed"]
        summaries[algorithm] = {
            metric: dict(mean=statistics.mean(values), std=statistics.pstdev(values))
            for metric in (
                "precision",
                "recall",
                "f1",
                "pr_auc_average_precision",
                "normal_false_positive_rate",
                "false_alerts_per_station_hour",
                "raw_event_count",
                "event_count",
                "detected_events",
                "event_recall",
                "detection_delay_mean_s",
                "detection_delay_p95_s",
            )
            if (values := [r[metric] for r in members if r.get(metric) is not None])
        }
    with tempfile.TemporaryDirectory(prefix=".benchmark-", dir=root) as temporary:
        stage = Path(temporary) / "results"
        stage.mkdir()
        pa = importlib.import_module("pyarrow")
        pq.write_table(
            pa.Table.from_pylist(prediction_rows), stage / "predictions.parquet", compression="zstd"
        )
        report = dict(
            schema_version="0.5.1-benchmark.1" if scale else "0.5-benchmark.1",
            mode=mode,
            scale=scale,
            ablation=ablation,
            features=columns,
            removed_features=removed,
            results=results,
            ten_site_diagnostic=supplemental_results,
            ten_site_independence=dict(
                strict_generalization=False,
                runs=len({r["run_id"] for r in supplemental_indices}),
                shared_group_ids=sorted(
                    {r["group_id"] for r in supplemental_indices}
                    & {r["group_id"] for r in train_indices}
                ),
                reason="shared weather source/date and station identities; "
                "never fit or calibrate on ten-site",
            ),
            summaries=summaries,
            seeds=policy.model_seeds,
            training_runs=len({r["run_id"] for r in train_indices}),
            test_independent_groups=len({r["group_id"] for r in test_indices}),
            limitations=[
                "simulation prerelease; no field calibration or innovation claim",
                "overlapping windows are correlated; seed SD is not a confidence interval",
                "business-rule-derived labels require the included rule baseline",
                "V0.5.1 reports all raw events, with endpoint eligibility separate from detection",
                "PR-AUC reported as non-interpolated average precision",
                "business_rule is the same M2 reference in every mode report",
            ],
            versions={
                name: importlib.import_module(name).__version__
                for name in ("sklearn", "numpy", "pyarrow")
            },
            feature_table_sha256=quality["tables"]["features"],
            threshold_selection="validation F1 highest threshold tie; single-normal-class "
            "validation uses discrete >= FPR<=1% (legacy uses original 99th percentile)",
            hyperparameters=dict(
                n_estimators=100,
                rf_max_depth=6,
                rf_min_samples_leaf=2,
                if_max_samples=256,
                ewma_alpha=0.3,
            ),
            **code_record(),
        )
        write_json(stage / "report.json", report)
        stage.rename(destination)
    return {k: v for k, v in report.items() if k != "results"}
