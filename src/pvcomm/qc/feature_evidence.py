"""Fixed-sample reference calculations from P0 and master evidence, without feature code."""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from pvcomm.storage.parquet import iter_jsonl


def reference_traffic(
    packets: list[dict[str, Any]],
    apdus: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    address: str,
    left: int,
    right: int,
) -> dict[str, Any]:
    tables = [
        sorted(
            (
                r
                for r in rows
                if address in (r.get("src_ip"), r.get("dst_ip"))
                and left <= max(r["ts_capture_utc_ns"], r.get("available_at") or 0) < right
            ),
            key=lambda r: max(r["ts_capture_utc_ns"], r.get("available_at") or 0),
        )
        for rows in (packets, apdus, objects)
    ]
    ps, ads, obs = tables
    lengths = [r["packet_len"] for r in ps]
    times = [r["ts_capture_utc_ns"] for r in ps]
    intervals = [(times[i] - times[i - 1]) / 1e9 for i in range(1, len(times))]
    result: dict[str, Any] = {}
    for name, up in (("up", True), ("down", False)):
        selected = [r for r in ps if (r["src_ip"] == address) == up]
        result[name + "_packets"] = len(selected)
        result[name + "_bytes"] = sum(r["packet_len"] for r in selected)
    result.update(
        payload_bytes=sum(r["tcp_payload_len"] for r in ps),
        packet_length_mean=sum(lengths) / len(lengths) if lengths else None,
        packet_length_std=statistics.pstdev(lengths) if lengths else None,
        iat_mean_s=sum(intervals) / len(intervals) if intervals else None,
        iat_p95_s=sorted(intervals)[math.ceil(0.95 * len(intervals)) - 1] if intervals else None,
        packet_rate=len(ps) * 1e9 / (right - left),
        retransmission_fraction=sum(bool(r["tcp_retransmission"]) for r in ps) / len(ps)
        if ps
        else 0,
        burst_fraction=sum(x < 0.01 for x in intervals) / len(intervals) if intervals else 0,
        active_span_s=(max(times) - min(times)) / 1e9 if times else 0,
        negative_confirmations=sum(r.get("negative_confirm") is True for r in ads),
        commands=sum(r.get("type_id") == 50 and r.get("cot") == 6 for r in ads),
        interrogations=sum(r.get("type_id") == 100 and r.get("cot") == 6 for r in ads),
        clock_syncs=sum(r.get("type_id") == 103 for r in ads),
        telemetry_objects=sum(r.get("type_id") in (1, 13, 30, 36) for r in obs),
        invalid_quality_objects=sum(bool(r.get("quality_raw")) for r in obs),
    )
    for name in ("I", "S", "U"):
        result[name.lower() + "_frames"] = sum(r["frame_type"] == name for r in ads)
    return result


def reference_business(
    master: list[dict[str, Any]],
    station: dict[str, Any],
    cursor: int,
) -> dict[str, Any]:
    history = sorted(
        (
            r
            for r in master
            if r.get("station_id") == station["station_id"]
            and max(r["available_at"], r["ts_event_utc_ns"]) < cursor
        ),
        key=lambda r: max(r["available_at"], r["ts_event_utc_ns"]),
    )
    required = (1001, 1002, 1003, 1008, 2001)
    latest = {}
    for row in history:
        if row["event_type"] == "master_observed_point":
            latest[row["ioa"]] = row
    values = {
        ioa: r["value"]
        if r.get("quality_valid") is True
        and isinstance(r.get("value"), (int, float))
        and math.isfinite(r["value"])
        else None
        for ioa, r in latest.items()
    }
    ages = {ioa: (cursor - r["available_at"]) / 1e9 for ioa, r in latest.items()}
    budget = 2 * station["telemetry_period_s"] + 1
    plans = [
        r
        for r in history
        if r["event_type"] == "master_maintenance_plan"
        and r["available_at"] <= r["window_start_utc_ns"]
    ]
    active = [r for r in plans if r["window_start_utc_ns"] <= cursor - 1 < r["window_end_utc_ns"]]
    grace = [
        r
        for r in plans
        if r["window_start_utc_ns"]
        <= cursor - 1
        < r["window_end_utc_ns"] + int(r["recovery_deadline_s"] * 1e9)
    ]
    result = dict(
        power_kw=values.get(1001),
        available_kw=values.get(1008),
        voltage_kv=values.get(1003),
        power_age_s=ages.get(1001),
        available_age_s=ages.get(1008),
        power_quality_known=float(values.get(1001) is not None),
        power_age_budget_ratio=ages[1001] / budget if 1001 in ages else None,
        fresh_point_coverage=sum(values.get(i) is not None and ages[i] <= budget for i in required)
        / len(required),
        known_maintenance_active=float(bool(active)),
        maintenance_recovery_remaining_s=(
            (grace[-1]["window_end_utc_ns"] + int(grace[-1]["recovery_deadline_s"] * 1e9) - cursor)
            / 1e9
            if grace
            else 0
        ),
        last_target_kw=None,
        command_age_s=None,
        confirmation_wait_s=0.0,
        execution_wait_s=0.0,
    )
    commands = [r for r in history if r["event_type"] == "master_command_sent"]
    if not commands:
        return result
    current = commands[-1]
    age = (cursor - current["sent_utc_ns"]) / 1e9
    result.update(last_target_kw=current["target_kw"], command_age_s=age)
    replies = [r for r in history if r["event_type"] == "master_command_confirmation"]
    matched = [
        r
        for r in replies
        if r.get("matched") is True
        and r.get("task_id") == current["task_id"]
        and r.get("connection_epoch") == current.get("connection_epoch")
    ]
    if not matched:
        same = [
            c
            for c in commands
            if c.get("connection_epoch") == current.get("connection_epoch")
            and c.get("ioa") == current.get("ioa")
            and abs(c["target_kw"] - current["target_kw"]) <= 0.1
        ]
        late = [
            r
            for r in replies
            if r.get("matched") is False
            and r.get("connection_epoch") == current.get("connection_epoch")
            and r.get("ioa") == current.get("ioa")
            and abs(r.get("target_kw", math.inf) - current["target_kw"]) <= 0.1
            and r["available_at"] >= current["sent_utc_ns"]
        ]
        if len(same) == len(late) == 1:
            matched = late
    if not matched:
        result["confirmation_wait_s"] = age
    elif matched[-1]["positive_confirmation"] is True:
        feedback = all(
            values.get(i) is not None and latest[i]["available_at"] >= current["sent_utc_ns"]
            for i in (1001, 1006)
        )
        feedback = feedback and abs(values[1006] - current["target_kw"]) <= max(
            0.1, station["capacity_kw"] * 0.01
        )
        if not feedback:
            result["execution_wait_s"] = age
    return result


def check_feature_evidence(
    catalogue: dict[str, dict[str, Any]],
    indices: list[dict[str, Any]],
    features: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    import json

    grouped: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, row in enumerate(indices):
        grouped[row["run_id"]][row["station_id"]].append(i)
    failures: list[dict[str, Any]] = []
    samples = apdu_count = segmented = 0

    def failure(value: dict[str, Any]) -> None:
        if len(failures) < 100:
            failures.append(value)

    for run_id, station_groups in grouped.items():
        root = Path(catalogue[run_id]["path"])
        config = json.loads((root / "power_config.json").read_bytes())
        manifest = json.loads((root / "manifest.json").read_bytes())
        stations = config["protocol"]["stations"]
        addresses = dict(
            zip((s["station_id"] for s in stations), manifest["network"]["addresses"], strict=True)
        )
        tables = [
            list(iter_jsonl([root / f"parsed/tshark-pdml-v2/{name}.jsonl"]))
            for name in ("packets", "iec104_apdu", "iec104_objects")
        ]
        master = list(iter_jsonl(sorted((root / "observed").glob("master_events*.jsonl"))))
        frame_times = {r["packet_index"]: r["ts_capture_utc_ns"] for r in tables[0]}
        for rows in tables:
            if any(r["run_id"] != run_id or r["capture_id"] != "primary" for r in rows):
                failure(dict(run_id=run_id, field="P0_identity"))
        for r in tables[1]:
            apdu_count += 1
            refs = r["source_frame_refs"]
            segmented += len(refs) > 1
            if (
                not refs
                or any(f not in frame_times for f in refs)
                or (r["available_at"] != max(frame_times[f] for f in refs))
            ):
                failure(dict(run_id=run_id, apdu_id=r["apdu_id"], field="reassembly_available_at"))
        application_times = {r["apdu_id"]: r["available_at"] for r in tables[1]}
        for obj in tables[2]:
            if obj["available_at"] != application_times.get(obj["apdu_id"]):
                failure(dict(run_id=run_id, apdu_id=obj["apdu_id"], field="object_available_at"))
        for station in stations:
            members = sorted(
                station_groups[station["station_id"]], key=lambda i: indices[i]["window_end_utc_ns"]
            )
            times = [indices[i]["window_end_utc_ns"] for i in members]
            selected = {members[0], members[len(members) // 2], members[-1]}
            for event in events:
                if (event["run_id"], event["station_id"]) != (run_id, station["station_id"]):
                    continue
                for boundary in (
                    event["start_utc_ns"],
                    event["observable_at_utc_ns"],
                    event["end_utc_ns"],
                ):
                    offset = bisect.bisect_left(times, boundary)
                    selected.update(
                        members[j] for j in (offset - 1, offset) if 0 <= j < len(members)
                    )
            for i in sorted(selected):
                index, actual = indices[i], features[i]
                left, right = index["window_start_utc_ns"], index["window_end_utc_ns"]
                expected = reference_traffic(
                    tables[0], tables[1], tables[2], addresses[station["station_id"]], left, right
                )
                own = reference_business(master, station, right)
                expected.update(own)
                peers = [reference_business(master, s, right) for s in stations if s != station]
                fresh = [
                    p
                    for p in peers
                    if p["power_kw"] is not None
                    and p["power_age_s"] is not None
                    and p["power_age_budget_ratio"] <= 1
                ]
                expected.update(
                    peer_fresh_power_coverage=len(fresh) / len(peers),
                    peer_mean_power_kw=statistics.mean(p["power_kw"] for p in fresh)
                    if fresh
                    else None,
                    peer_mean_power_age_s=statistics.mean(p["power_age_s"] for p in fresh)
                    if fresh
                    else None,
                )
                for name, value in expected.items():
                    got = actual[name]
                    same = (
                        got is None
                        if value is None
                        else (
                            got is not None
                            and math.isclose(got, value, rel_tol=1e-10, abs_tol=1e-10)
                        )
                    )
                    if not same:
                        failure(
                            dict(
                                sample_id=index["sample_id"], field=name, expected=value, actual=got
                            )
                        )
                samples += 1
    return dict(
        passed=not failures,
        checked_samples=samples,
        checked_apdus=apdu_count,
        segmented_apdus=segmented,
        failures=failures,
        sampling="each run/station first,middle,last and nearest event boundaries",
        input_sources="P0 protocol tables and observed/master_events only",
    )
