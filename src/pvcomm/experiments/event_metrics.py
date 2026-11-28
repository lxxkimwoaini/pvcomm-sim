"""Strict sampled event evaluation; no point adjustment or inferred detections."""

from __future__ import annotations

import importlib
import statistics
from collections import defaultdict
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.pipeline.coverage import classify_event, event_hit


def evaluate_events(
    indices: list[dict[str, Any]],
    predictions: list[int],
    labels: list[int],
    events: list[dict[str, Any]],
    episodes: list[tuple[str, str, list[int]]],
    policy: DatasetPolicy,
) -> dict[str, Any]:
    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in indices:
        observed[row["run_id"], row["station_id"]].append(row["window_end_utc_ns"])
    used: set[int] = set()
    outcomes = []
    for event in sorted(events, key=lambda e: (e["start_utc_ns"], e["run_id"], e["station_id"])):
        key = event["run_id"], event["station_id"]
        times = observed.get(key, [])
        full_history = event.get("warmup_end_utc_ns")
        full_history = full_history + policy.window_s * 10**9 if full_history is not None else None
        status = classify_event(event, times, full_history)
        hits = [t for t in times if event_hit(event, t)]
        candidates = [
            (t, i)
            for i, (run, station, alerts) in enumerate(episodes)
            if i not in used and (run, station) == key
            for t in alerts
            if event_hit(event, t)
        ]
        first = None
        episode_id = None
        if candidates:
            first, episode_id = min(candidates)
            used.add(episode_id)
        online = event.get("observable_at_utc_ns", event["start_utc_ns"])
        history_ready = min(times) if times else full_history
        history_wait = max(0, history_ready - online) / 1e9 if history_ready else None
        first_hit = min(hits) if hits else None
        sampling_wait = (
            (first_hit - max(online, history_ready or online)) / 1e9 if first_hit else None
        )
        outcomes.append(
            dict(
                event,
                evaluation_status=status,
                detected=first is not None,
                missed=status == "evaluable" and first is None,
                matched_episode=episode_id,
                first_alert_utc_ns=first,
                first_eligible_endpoint_utc_ns=first_hit,
                delay_s=(first - online) / 1e9 if first is not None else None,
                delay_from_violation_s=(first - event["start_utc_ns"]) / 1e9
                if first is not None
                else None,
                history_wait_s=history_wait,
                sampling_wait_s=sampling_wait,
                detection_after_first_endpoint_s=(first - first_hit) / 1e9
                if first is not None and first_hit is not None
                else None,
                # Censoring is an evidence property, never a synonym for missing.
                censored=event.get("run_end_censored", False),
            )
        )
    endpoint_labels = {
        (r["run_id"], r["station_id"], r["window_end_utc_ns"]): y
        for r, y in zip(indices, labels, strict=True)
    }
    false_episodes = sum(
        all(endpoint_labels[run, station, t] == 0 for t in times)
        for run, station, times in episodes
    )
    duplicate_episodes = sum(
        i not in used
        and any(
            event_hit(e, t)
            for e in events
            if (e["run_id"], e["station_id"]) == (run, station)
            for t in times
        )
        for i, (run, station, times) in enumerate(episodes)
    )
    # Endpoint slots, capped at step; never count a sampling gap as observed time.
    normal_ns = 0
    for key, times in observed.items():
        previous = None
        for t in sorted(set(times)):
            left = max(t - policy.step_s * 10**9, previous if previous is not None else 0)
            if endpoint_labels[*key, t] == 0:
                normal_ns += t - left
            previous = t
    hours = normal_ns / 3.6e12
    evaluable = [r for r in outcomes if r["evaluation_status"] == "evaluable"]
    delays = [r["delay_s"] for r in outcomes if r["detected"]]
    np = importlib.import_module("numpy")
    return dict(
        raw_event_count=len(outcomes),
        event_count=len(evaluable),
        independent_violation_runs=len({e["run_id"] for e in events}),
        independent_evaluable_runs=len({e["run_id"] for e in evaluable}),
        event_status_counts={
            status: sum(r["evaluation_status"] == status for r in outcomes)
            for status in (
                "evaluable",
                "insufficient_history",
                "no_sample_endpoint",
                "not_online_observable_at_endpoint",
            )
        },
        detected_events=len(delays),
        missed_events=len(evaluable) - len(delays),
        event_recall=len(delays) / len(evaluable) if evaluable else None,
        raw_event_detection_coverage=len(delays) / len(outcomes) if outcomes else None,
        run_end_censored_events=sum(e.get("run_end_censored", False) for e in events),
        detection_delay_mean_s=statistics.mean(delays) if delays else None,
        detection_delay_p95_s=float(np.quantile(delays, 0.95)) if delays else None,
        normal_endpoint_exposure_station_hours=hours,
        false_alert_episodes=false_episodes,
        duplicate_alert_episodes=duplicate_episodes,
        unmatched_non_normal_episodes=len(episodes)
        - len(used)
        - false_episodes
        - duplicate_episodes,
        false_alerts_per_station_hour=false_episodes / hours if hours else None,
        event_outcomes=outcomes,
        exposure_definition="union of normal endpoint slots of at most step_s; gaps excluded",
        delay_reference="online observable start; no point adjustment; one episode per event",
    )
