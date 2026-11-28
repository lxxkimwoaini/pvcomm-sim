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
from pvcomm.pipeline.traffic import jsonl_records
from pvcomm.qc.labels import VERSION as LABEL_QC_VERSION

VERSION = "v04-pair-qc-v2"


def business_contrast(config: PairConfig, members: dict[str, list[EventLabel]]) -> dict[str, Any]:
    """Require the intended contrast on every target, with quiet control stations."""
    stations = {s.station_id for s in config.normal.simulation.protocol.stations}
    targets = set(config.changed.target_stations())
    indexed = {name: {r.station_id: r for r in rows} for name, rows in members.items()}
    identities = set(members) == {"normal", "changed"} and all(
        len(rows) == len(stations) and {r.station_id for r in rows} == stations
        for rows in members.values()
    )
    results = {}
    for station in sorted(stations):
        normal = indexed.get("normal", {}).get(station)
        changed = indexed.get("changed", {}).get(station)
        known = all(
            r is not None and r.label_status == "valid" and type(r.service_violation) is bool
            for r in (normal, changed)
        )
        target = station in targets
        expected = {
            "P03": "reception_freshness_expired",
            "P03_recovery": "reception_freshness_expired",
            "P04": "feedback_missing",
            "P06": "confirmation_timeout",
        }[config.pair_type]
        expected_finding = bool(
            changed
            and any(
                f.outcome == expected and f.service_violation is True and f.label_status == "valid"
                for f in changed.findings
            )
        )
        normal_outcome = {"P04": "accepted_complete", "P06": "rejected"}.get(config.pair_type)
        normal_business = bool(
            normal
            and (
                normal_outcome is None
                or any(
                    f.outcome == normal_outcome
                    and f.label_status == "valid"
                    and f.service_violation is False
                    for f in normal.findings
                )
            )
        )
        passed = bool(
            known
            and normal is not None
            and changed is not None
            and normal.service_violation is False
            and (
                changed.service_violation is True if target else changed.service_violation is False
            )
            and (not target or (expected_finding and normal_business))
        )
        results[station] = dict(
            targeted=target,
            normal_status=normal.label_status if normal else None,
            changed_status=changed.label_status if changed else None,
            normal_violation=normal.service_violation if normal else None,
            changed_violation=changed.service_violation if changed else None,
            expected_changed_finding=expected_finding if target else None,
            expected_normal_business=normal_business if target else None,
            passed=passed,
        )
    return dict(
        identities=identities,
        stations=results,
        targets_passed=all(r["passed"] for r in results.values() if r["targeted"]),
        controls_passed=all(r["passed"] for r in results.values() if not r["targeted"]),
        passed=identities and bool(targets) and all(r["passed"] for r in results.values()),
    )


def traffic_statistics(root: Path, start_s: float, end_s: float) -> dict[str, float | int]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    clock = manifest["scene_clock"]["start_mono_ns"]
    origin = manifest["clock_origin"]
    start = origin["ts_utc_ns"] + clock - origin["ts_mono_ns"] + int(start_s * 1e9)
    end = start + int((end_s - start_s) * 1e9)
    addresses = set(manifest["network"]["addresses"])
    packet_times: dict[str, list[int]] = {"up": [], "down": []}
    byte_counts = {"up": 0, "down": 0}
    for row in jsonl_records(root / "parsed" / PARSER_VERSION / "packets.jsonl"):
        if not start <= row["ts_capture_utc_ns"] < end:
            continue
        direction = (
            "up" if row["src_ip"] in addresses else "down" if row["dst_ip"] in addresses else None
        )
        if direction is not None:
            packet_times[direction].append(row["ts_capture_utc_ns"])
            byte_counts[direction] += row["packet_len"]
    result: dict[str, float | int] = {"window_duration_s": end_s - start_s}
    for direction in ("up", "down"):
        times = sorted(packet_times[direction])
        gaps = sorted((b - a) / 1e9 for a, b in zip(times, times[1:], strict=False))
        result[f"{direction}_packets"] = len(times)
        result[f"{direction}_bytes"] = byte_counts[direction]
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
    labels: dict[str, list[EventLabel]] = {}
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
        begin = [
            r
            for path in sorted((root / "observed").glob("master_events*.jsonl"))
            for r in jsonl_records(path)
            if r["event_type"] == "master_business_started"
        ]
        starts &= len(begin) == 1 and 0 <= begin[0]["sim_elapsed_ms"] <= 100
        members_ok &= (
            manifest["status"] == "completed"
            and not manifest["code_dirty"]
            and not manifest["cleanup_errors"]
        )
        reports = [
            p
            for p in (root / "qc").glob("*/report.json")
            if not p.parent.name.startswith("v04-label-qc-")
        ]
        members_ok &= bool(reports) and all(json.loads(p.read_bytes())["passed"] for p in reports)
        label_qc = root / "qc" / LABEL_QC_VERSION / "report.json"
        label_file = root / "labels" / LABEL_VERSION / "event_labels.jsonl"
        label_report = json.loads(label_qc.read_bytes())
        labels_ok &= label_report["passed"] and label_report.get("label_sha256") == file_sha256(
            label_file
        )
        sources[f"{record['run_id']}/qc/{LABEL_QC_VERSION}/report.json"] = file_sha256(label_qc)
        with label_file.open(encoding="utf-8") as stream:
            labels[record["member"]] = [EventLabel.model_validate_json(s) for s in stream]
        labels_ok &= all(r.run_id == record["run_id"] for r in labels[record["member"]])
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
    contrast = business_contrast(config, labels)
    data_quality = bool(identities and shared and starts and members_ok and labels_ok)
    result = dict(
        qc_version=VERSION,
        pair_id=config.pair_id,
        pair_type=config.pair_type,
        shared_conditions=bool(shared),
        business_clock_start=bool(starts),
        member_quality=bool(members_ok),
        label_quality=bool(labels_ok),
        identities=identities,
        data_quality_passed=data_quality,
        business_contrast=contrast,
        contrast_observed=contrast["passed"],
        traffic_statistics=stats,
        traffic_relative_distances=distance,
        similarity_selection="not_performed_descriptive_distances_only",
        source_hashes=sources,
        passed=data_quality and contrast["passed"],
    )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
