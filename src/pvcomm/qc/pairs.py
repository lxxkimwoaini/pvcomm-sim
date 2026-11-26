"""Shared-condition audit and fixed traffic summaries; no packet modification."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from pvcomm.config.pairs import PairConfig
from pvcomm.config.scenes import ScenarioConfig, canonical_hash
from pvcomm.domain.labels import EventLabel
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.pipeline.labels import VERSION as LABEL_VERSION
from pvcomm.pipeline.labels import file_sha256
from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION
from pvcomm.storage.parquet import read_jsonl

VERSION = "v04-pair-qc-v1"


def traffic_statistics(root: Path, start_s: float, end_s: float) -> dict[str, float | int]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    clock = manifest["scene_clock"]["start_mono_ns"]
    origin = manifest["clock_origin"]
    start = origin["ts_utc_ns"] + clock - origin["ts_mono_ns"] + int(start_s * 1e9)
    end = start + int((end_s - start_s) * 1e9)
    addresses = set(manifest["network"]["addresses"])
    packets = read_jsonl([root / "parsed" / PARSER_VERSION / "packets.jsonl"])
    result: dict[str, float | int] = {"window_duration_s": end_s - start_s}
    for direction in ("up", "down"):
        own = [
            r
            for r in packets
            if start <= r["ts_capture_utc_ns"] < end
            and (r["src_ip"] in addresses if direction == "up" else r["dst_ip"] in addresses)
        ]
        times = sorted(r["ts_capture_utc_ns"] for r in own)
        gaps = sorted((b - a) / 1e9 for a, b in zip(times, times[1:], strict=False))
        result[f"{direction}_packets"] = len(own)
        result[f"{direction}_bytes"] = sum(r["packet_len"] for r in own)
        result[f"{direction}_active_duration_s"] = (times[-1] - times[0]) / 1e9 if times else 0.0
        result[f"{direction}_iat_mean_s"] = statistics.mean(gaps) if gaps else 0.0
        result[f"{direction}_iat_p50_s"] = statistics.median(gaps) if gaps else 0.0
        result[f"{direction}_iat_p95_s"] = (
            gaps[min(len(gaps) - 1, int(0.95 * len(gaps)))] if gaps else 0.0
        )
    return result


def qc_pair(directory: Path) -> dict[str, Any]:
    destination = directory / "qc" / VERSION
    if destination.exists():
        raise FileExistsError(f"pair QC exists: {destination}")
    config = PairConfig.model_validate_json((directory / "pair.json").read_bytes())
    ledger = json.loads((directory / "pair-manifest.json").read_bytes())
    shared, starts, members_ok, labels_ok = True, True, True, True
    labels = []
    measurements = []
    sources = {"pair.json": file_sha256(directory / "pair.json")}
    rows = ledger["members"]
    identities = (
        len(rows) == 2
        and {r["member"] for r in rows} == {"normal", "changed"}
        and len({r["run_id"] for r in rows}) == 2
    )
    for record in rows:
        if record["run_dir"] != record["run_id"] or Path(record["run_id"]).name != record["run_id"]:
            raise ValueError("invalid pair member path")
        root = directory / record["run_dir"]
        effective = ScenarioConfig.model_validate_json(
            (root / "truth/scenario/effective.json").read_bytes()
        )
        manifest = json.loads((root / "manifest.json").read_bytes())
        expected = config.normal if record["member"] == "normal" else config.changed.background
        actual = effective.model_dump(mode="json")
        wanted = expected.model_dump(mode="json")
        wanted["simulation"]["protocol"]["run_id"] = record["run_id"]
        shared &= actual == wanted
        shared &= canonical_hash(config.shared_conditions(effective)) == config.shared_sha256()
        shared &= ledger["shared_sha256"] == config.shared_sha256()
        master = read_jsonl(list((root / "observed").glob("master_events*.jsonl")))
        begin = [r for r in master if r["event_type"] == "master_business_started"]
        starts &= len(begin) == 1 and 0 <= begin[0]["sim_elapsed_ms"] <= 100
        members_ok &= (
            manifest["status"] == "completed"
            and not manifest["code_dirty"]
            and not manifest["cleanup_errors"]
        )
        reports = [
            p for p in (root / "qc").glob("*/report.json") if p.parent.name != LABEL_QC_VERSION
        ]
        members_ok &= bool(reports) and all(json.loads(p.read_bytes())["passed"] for p in reports)
        label_qc = root / "qc" / LABEL_QC_VERSION / "report.json"
        labels_ok &= json.loads(label_qc.read_bytes())["passed"]
        label_file = root / "labels" / LABEL_VERSION / "event_labels.jsonl"
        labels.append(
            (
                record["member"],
                [
                    EventLabel.model_validate_json(s)
                    for s in label_file.read_text(encoding="utf-8").splitlines()
                ],
            )
        )
        sources[f"{record['run_id']}/manifest.json"] = file_sha256(root / "manifest.json")
        sources[f"{record['run_id']}/labels/{LABEL_VERSION}/event_labels.jsonl"] = file_sha256(
            label_file
        )
        fault = config.changed.fault
        measurements.append(
            (
                record["member"],
                traffic_statistics(
                    root, max(0, fault.start_s - 5), fault.start_s + fault.duration_s + 20
                ),
            )
        )
    stats = dict(measurements)
    distance = (
        {
            k: abs(stats["normal"][k] - stats["changed"][k])
            / max(abs(stats["normal"][k]), abs(stats["changed"][k]), 1e-9)
            for k in stats.get("normal", {})
        }
        if identities
        else {}
    )
    # Descriptive distances only: no post-hoc threshold or model-based pair selection.
    normal_violation = any(
        r.service_violation for name, records in labels if name == "normal" for r in records
    )
    changed_violation = any(
        r.service_violation for name, records in labels if name == "changed" for r in records
    )
    result = dict(
        qc_version=VERSION,
        pair_id=config.pair_id,
        pair_type=config.pair_type,
        shared_conditions=bool(shared),
        business_clock_start=bool(starts),
        member_quality=bool(members_ok),
        label_quality=bool(labels_ok),
        identities=identities,
        contrast_observed=not normal_violation and changed_violation,
        traffic_statistics=stats,
        traffic_relative_distances=distance,
        similarity_selection="not_performed_descriptive_distances_only",
        source_hashes=sources,
        passed=bool(identities and shared and starts and members_ok and labels_ok),
    )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
