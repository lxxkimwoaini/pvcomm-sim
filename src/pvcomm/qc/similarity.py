"""Fixed traffic distances and independent normal-repeat calibration, no model scores."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pvcomm.config.pairs import PairConfig
from pvcomm.config.scenes import canonical_hash
from pvcomm.config.similarity import CalibratedSimilarity, SimilarityPolicy
from pvcomm.pipeline.labels import file_sha256
from pvcomm.pipeline.traffic import summarize_traffic


def histogram_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    if a["upper_bounds"] != b["upper_bounds"]:
        raise ValueError("histogram contracts differ")
    n, m = sum(a["counts"]), sum(b["counts"])
    if not n or not m:
        return float(bool(n or m))
    score = 0.0
    for x, y in zip(a["counts"], b["counts"], strict=True):
        p, q = x / n, y / m
        middle = (p + q) / 2
        if p:
            score += 0.5 * p * math.log2(p / middle)
        if q:
            score += 0.5 * q * math.log2(q / middle)
    return math.sqrt(max(0.0, min(1.0, score)))


def traffic_distances(
    normal: dict[str, Any], changed: dict[str, Any]
) -> dict[str, dict[str, float]]:
    if normal["window_s"] != changed["window_s"] or set(normal["summaries"]) != set(
        changed["summaries"]
    ):
        raise ValueError("matching requires identical analysis windows and station coverage")
    result = {}
    for scope, directions in normal["summaries"].items():
        own = {}
        for direction, values in directions.items():
            other = changed["summaries"][scope][direction]
            if set(values["scalars"]) != set(other["scalars"]) or set(
                values["distributions"]
            ) != set(other["distributions"]):
                raise ValueError("traffic feature contracts differ")
            for feature, value in values["scalars"].items():
                alternate = other["scalars"][feature]
                # Millisecond resolution floor for intervals; 1 count/byte otherwise.
                floor = 0.001 if feature.startswith("iat_") else 1e-9
                own[f"{direction}.{feature}"] = abs(value - alternate) / max(
                    abs(value), abs(alternate), floor
                )
            for feature, histogram in values["distributions"].items():
                own[f"{direction}.{feature}_distribution"] = histogram_distance(
                    histogram, other["distributions"][feature]
                )
        result[scope] = own
    return result


def calibrate_thresholds(
    policy: SimilarityPolicy, records: list[dict[str, Any]]
) -> CalibratedSimilarity:
    if len(records) < policy.minimum_calibration_pairs:
        raise ValueError("at least six independent normal-repeat calibration pairs are required")
    maxima: dict[str, float] = {}
    hashes, run_ids, pair_ids = {}, [], []
    for record in records:
        if record["purpose"] != "independent_normal_repeat_calibration" or not record["passed"]:
            raise ValueError("threshold calibration requires independently valid normal repeats")
        pair_ids.append(record["pair_id"])
        run_ids.extend(record["run_ids"])
        hashes[record["pair_id"]] = canonical_hash(record)
        for scope, values in record["distances"].items():
            category = "global" if scope == "global" else "local"
            for feature, value in values.items():
                key = f"{category}.{feature}"
                maxima[key] = max(maxima.get(key, 0.0), value)
    return CalibratedSimilarity(
        policy_sha256=canonical_hash(policy.model_dump(mode="json")),
        pair_ids=tuple(pair_ids),
        run_ids=tuple(run_ids),
        source_hashes=hashes,
        thresholds={key: min(1.0, max(0.05, 1.25 * value)) for key, value in maxima.items()},
    )


def match_traffic(
    normal: dict[str, Any],
    changed: dict[str, Any],
    policy: SimilarityPolicy,
    calibration: CalibratedSimilarity,
) -> dict[str, Any]:
    if calibration.policy_sha256 != canonical_hash(policy.model_dump(mode="json")):
        raise ValueError("thresholds were frozen under another feature policy")
    if {normal["run_id"], changed["run_id"]} & set(calibration.run_ids):
        raise ValueError("calibration runs cannot be reused as evaluation candidates")
    distances = traffic_distances(normal, changed)
    scopes = {}
    for scope, features in distances.items():
        category = "global" if scope == "global" else "local"
        failed = [
            feature
            for feature, value in features.items()
            if value > calibration.thresholds[f"{category}.{feature}"]
        ]
        scopes[scope] = dict(passed=not failed, failed_features=failed, distances=features)
    return dict(
        policy_sha256=calibration.policy_sha256,
        calibration_sha256=canonical_hash(calibration.model_dump(mode="json")),
        scopes=scopes,
        matched=all(r["passed"] for r in scopes.values()),
        nonmatch_is_invalid_experiment=False,
    )


def evaluate_pair_similarity(
    directory: Path, policy: SimilarityPolicy, calibration: CalibratedSimilarity
) -> dict[str, Any]:
    from pvcomm.qc.pairs import VERSION as PAIR_QC_VERSION

    destination = directory / "qc/v04-traffic-similarity-v1"
    if destination.exists():
        raise FileExistsError(f"similarity evaluation exists: {destination}")
    config = PairConfig.model_validate_json((directory / "pair.json").read_bytes())
    ledger = json.loads((directory / "pair-manifest.json").read_bytes())
    quality_path = directory / "qc" / PAIR_QC_VERSION / "report.json"
    quality = json.loads(quality_path.read_bytes())
    summaries, sources = (
        {},
        {
            "pair.json": file_sha256(directory / "pair.json"),
            str(quality_path.relative_to(directory)): file_sha256(quality_path),
        },
    )
    for record in ledger["members"]:
        if record["run_dir"] != record["run_id"] or Path(record["run_id"]).name != record["run_id"]:
            raise ValueError("unsafe pair member path")
        root = directory / record["run_dir"]
        summaries[record["member"]] = summarize_traffic(
            root,
            max(0, config.changed.fault.start_s - policy.pre_event_s),
            config.changed.fault.start_s + config.changed.fault.duration_s + policy.post_event_s,
            config.changed.target_stations(),
            policy,
        )
        sources[f"{record['run_id']}/primary.pcap"] = file_sha256(root / "raw/pcap/primary.pcap")
    result = match_traffic(summaries["normal"], summaries["changed"], policy, calibration)
    result.update(
        pair_id=config.pair_id,
        pair_type=config.pair_type,
        data_quality_passed=quality["data_quality_passed"],
        business_contrast=quality["contrast_observed"],
        summaries=summaries,
        source_hashes=sources,
        qc_version="v04-traffic-similarity-v1",
        hard_pair_selected=quality["passed"] and result["matched"],
        evaluation_valid=quality["data_quality_passed"],
    )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
