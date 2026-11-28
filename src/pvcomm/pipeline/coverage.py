"""Evidence-only event and endpoint profiles, computed before model selection."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pvcomm.domain.labels import EventLabel
from pvcomm.storage.parquet import iter_jsonl

SCALES = {"W10": (10, 1), "W30": (30, 1), "W60": (60, 5)}


def run_bounds(record: dict[str, Any]) -> tuple[int, int, int]:
    """Read actual business end; never invent history after a partial run."""
    import json

    root = Path(record["path"])
    config = json.loads((root / "power_config.json").read_bytes())
    origin = record["scene_clock"]["start_utc_ns"]
    ends = [
        max(r["available_at"], r["ts_event_utc_ns"])
        for r in iter_jsonl(sorted((root / "observed").glob("master_events*.jsonl")))
        if r["event_type"] == "master_business_finished"
    ]
    if len(ends) != 1:
        raise ValueError("missing or ambiguous actual business end")
    end = min(origin + int(record["duration_s"] * 1e9), ends[0])
    return origin, origin + int(config["protocol"]["warmup_s"] * 1e9), end


def service_events(catalogue: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for identity, record in sorted(catalogue.items()):
        _, warmup_end, end_run = run_bounds(record)
        for raw in iter_jsonl([Path(record["label_path"])]):
            label = EventLabel.model_validate(raw)
            findings = sorted(
                (f for f in label.findings if f.service_violation and f.label_status == "valid"),
                key=lambda f: (f.start_utc_ns, f.finding_id),
            )
            merged: list[dict[str, Any]] = []
            for f in findings:
                end = min(f.end_utc_ns if f.end_utc_ns is not None else end_run, end_run)
                if merged and f.start_utc_ns <= merged[-1]["end_utc_ns"]:
                    event = merged[-1]
                    event["end_utc_ns"] = max(event["end_utc_ns"], end)
                    event["findings"].append(f.model_dump(mode="json"))
                else:
                    merged.append(
                        dict(
                            run_id=identity,
                            station_id=label.station_id,
                            start_utc_ns=f.start_utc_ns,
                            end_utc_ns=end,
                            findings=[f.model_dump(mode="json")],
                        )
                    )
            for event in merged:
                fs = event["findings"]
                event.update(
                    event_id=hashlib.sha256(
                        f"{identity}:{label.station_id}:{event['start_utc_ns']}".encode()
                    ).hexdigest(),
                    observable_at_utc_ns=min(f["observable_at_utc_ns"] for f in fs),
                    # An unresolved superseded transaction is distinct from a
                    # violation still open at the end of evidence collection.
                    recovery_censored=any(f["recovery_censored"] for f in fs),
                    run_end_censored=any(
                        f["recovery_censored"]
                        and f["superseded_at_utc_ns"] is None
                        and (f["end_utc_ns"] is None or f["end_utc_ns"] >= end_run)
                        for f in fs
                    ),
                    fault_types=record["fault_types"],
                    normal_scene=record.get("normal_scene"),
                    warmup_end_utc_ns=warmup_end,
                    business_end_utc_ns=end_run,
                )
                events.append(event)
    return events


def event_hit(event: dict[str, Any], endpoint: int) -> bool:
    """t-1ns semantics; online eligibility follows each finding's own interval."""
    cursor = endpoint - 1
    if not event["start_utc_ns"] <= cursor < event["end_utc_ns"]:
        return False
    findings = event.get("findings")
    if findings:
        return any(
            max(f["start_utc_ns"], f["observable_at_utc_ns"])
            <= cursor
            < (f["end_utc_ns"] if f["end_utc_ns"] is not None else event["end_utc_ns"])
            for f in findings
        )
    return bool(cursor >= event.get("observable_at_utc_ns", event["start_utc_ns"]))


def classify_event(
    event: dict[str, Any], times: list[int], history_ready: int | None = None
) -> str:
    if any(event_hit(event, t) for t in times):
        return "evaluable"
    first = min(times) if times else history_ready
    if first is not None and event["end_utc_ns"] < first:
        return "insufficient_history"
    if any(event["start_utc_ns"] <= t - 1 < event["end_utc_ns"] for t in times):
        return "not_online_observable_at_endpoint"
    return "no_sample_endpoint"


def profile_records(rows: list[dict[str, Any]]) -> dict[str, Any]:
    catalogue = {r["run_id"]: r for r in rows}
    events = service_events(catalogue)
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_run[event["run_id"]].append(event)
    profiles = []
    for row in sorted(rows, key=lambda r: r["run_id"]):
        origin, warm, end = run_bounds(row)
        labels = list(iter_jsonl([Path(row["label_path"])]))
        scales = {}
        for name, (window, step) in SCALES.items():
            times = list(range(warm + window * 10**9, end + 1, step * 10**9))
            first = warm + window * 10**9
            statuses = Counter(classify_event(e, times, first) for e in by_run[row["run_id"]])
            positive = sum(
                any(
                    f["service_violation"] is True
                    and f["label_status"] == "valid"
                    and f["start_utc_ns"]
                    <= t - 1
                    < (f["end_utc_ns"] if f["end_utc_ns"] is not None else end)
                    for f in label["findings"]
                )
                for t in times
                for label in labels
            )
            scales[name] = dict(
                windows=len(times) * row["site_count"],
                positives=positive,
                normal=len(times) * row["site_count"] - positive,
                event_status=dict(statuses),
                evaluable_events=statuses["evaluable"],
                evaluable_event_ids=[
                    e["event_id"]
                    for e in by_run[row["run_id"]]
                    if classify_event(e, times, first) == "evaluable"
                ],
                overlap_ratio=1 - step / window,
            )
        # Exact normal station-time union, independent of endpoint sampling.
        abnormal_ns = sum(
            e["end_utc_ns"] - max(origin, e["start_utc_ns"]) for e in by_run[row["run_id"]]
        )
        profiles.append(
            dict(
                run_id=row["run_id"],
                site_count=row["site_count"],
                group_keys=row["group_keys"],
                normal_scene=row.get("normal_scene"),
                fault_types=row["fault_types"],
                events=len(by_run[row["run_id"]]),
                normal_station_hours=((end - origin) * row["site_count"] - abnormal_ns) / 3.6e12,
                scales=scales,
            )
        )
    return dict(runs=profiles, events=events, scales=SCALES)
