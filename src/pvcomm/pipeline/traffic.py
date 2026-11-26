"""Bounded P0 scan and disk sort for fixed flow summaries. No Oracle measurements."""

from __future__ import annotations

import bisect
import json
import math
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from pvcomm.config.similarity import SimilarityPolicy
from pvcomm.pipeline.iec104_parser import PARSER_VERSION

VERSION = "v04-traffic-summary-v1"


def jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    """Reject oversized/incomplete records, with memory bounded by one source row."""
    with path.open("rb") as stream:
        while line := stream.readline(2 * 1024 * 1024 + 1):
            if len(line) > 2 * 1024 * 1024 or not line.endswith(b"\n"):
                raise ValueError(f"oversized or incomplete JSONL record: {path}")
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("JSONL record must be an object")
                yield row


def _distribution(values: list[int], bounds: tuple[float, ...] | tuple[int, ...]) -> dict[str, Any]:
    return dict(upper_bounds=[*bounds, None], counts=values)


def _summary(
    rows: Iterator[tuple[int, int]], duration: int, policy: SimilarityPolicy
) -> dict[str, Any]:
    lengths = [0] * (len(policy.length_bounds) + 1)
    gaps = [0] * (len(policy.iat_bounds_s) + 1)
    rates = [0] * duration
    first, previous = None, None
    count, total_bytes, gap_total = 0, 0, 0.0
    burst_size, bursts, burst_packets = 0, 0, 0
    maximum_gap = 0.0
    for relative_ns, size in rows:
        sec = relative_ns / 1e9
        rates[min(duration - 1, int(sec))] += 1
        count += 1
        total_bytes += size
        lengths[bisect.bisect_left(policy.length_bounds, size)] += 1
        if previous is not None:
            gap = (relative_ns - previous) / 1e9
            gaps[bisect.bisect_left(policy.iat_bounds_s, gap)] += 1
            gap_total += gap
            maximum_gap = max(maximum_gap, gap)
            if gap > policy.burst_gap_s:
                if burst_size >= policy.burst_min_packets:
                    bursts, burst_packets = bursts + 1, burst_packets + burst_size
                burst_size = 0
        else:
            first = relative_ns
        burst_size += 1
        previous = relative_ns
    if burst_size >= policy.burst_min_packets:
        bursts, burst_packets = bursts + 1, burst_packets + burst_size
    rate_histogram = [0] * (len(policy.rate_bounds) + 1)
    for rate in rates:
        rate_histogram[bisect.bisect_left(policy.rate_bounds, rate)] += 1

    def gap_quantile(probability: float) -> float:
        if count < 2:
            return 0.0
        rank, cumulative = math.ceil(probability * (count - 1)), 0
        for index, n in enumerate(gaps):
            cumulative += n
            if cumulative >= rank:
                return (
                    policy.iat_bounds_s[index] if index < len(policy.iat_bounds_s) else maximum_gap
                )
        raise ValueError("gap histogram count mismatch")

    return dict(
        scalars=dict(
            packets=count,
            bytes=total_bytes,
            packet_length_mean=total_bytes / count if count else 0.0,
            active_duration_s=(previous - first) / 1e9
            if first is not None and previous is not None
            else 0.0,
            iat_mean_s=gap_total / (count - 1) if count > 1 else 0.0,
            iat_p50_s=gap_quantile(0.5),
            iat_p95_s=gap_quantile(0.95),
            packet_rate_s=count / duration,
            byte_rate_s=total_bytes / duration,
            peak_packets_per_s=max(rates),
            burst_count=bursts,
            burst_packet_fraction=burst_packets / count if count else 0.0,
        ),
        distributions=dict(
            packet_length=_distribution(lengths, policy.length_bounds),
            iat_s=_distribution(gaps, policy.iat_bounds_s),
            packet_rate_s=_distribution(rate_histogram, policy.rate_bounds),
        ),
    )


def summarize_traffic(
    root: Path, start_s: int, end_s: int, station_ids: tuple[str, ...], policy: SimilarityPolicy
) -> dict[str, Any]:
    if start_s < 0 or end_s <= start_s or end_s - start_s > 3600:
        raise ValueError("traffic analysis needs an explicit window of at most one hour")
    if not station_ids or len(set(station_ids)) != len(station_ids) or "global" in station_ids:
        raise ValueError("explicit distinct local station coverage is required")
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest["status"] != "completed":
        raise ValueError("incomplete runs are not similarity candidates")
    power = json.loads((root / "power_config.json").read_bytes())
    stations = power["protocol"]["stations"]
    if end_s > power["protocol"]["duration_s"] or not set(station_ids) <= {
        s["station_id"] for s in stations
    }:
        raise ValueError("analysis window or target stations outside the recorded scenario")
    addresses = dict(
        zip(manifest["network"]["addresses"], (s["station_id"] for s in stations), strict=True)
    )
    clock, origin = manifest["scene_clock"], manifest["clock_origin"]
    start = origin["ts_utc_ns"] + clock["start_mono_ns"] - origin["ts_mono_ns"] + start_s * 10**9
    end = start + (end_s - start_s) * 10**9
    path = root / "parsed" / PARSER_VERSION / "packets.jsonl"
    # SQLite is a temporary external sort, never a persistent packet database.
    with tempfile.TemporaryDirectory(prefix="pvcomm-traffic-") as temporary:
        database = Path(temporary) / "sort.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute(
                "CREATE TABLE packets (ts INTEGER, station TEXT, direction TEXT, size INTEGER)"
            )
            for row in jsonl_records(path):
                if row["run_id"] != manifest["run_id"] or row["capture_id"] != "primary":
                    raise ValueError("cross-run or non-P0 packet source")
                if not start <= row["ts_capture_utc_ns"] < end:
                    continue
                src, dst = row.get("src_ip"), row.get("dst_ip")
                if (src in addresses) == (dst in addresses):
                    continue
                station = addresses[src if src in addresses else dst]
                direction = "up" if src in addresses else "down"
                connection.execute(
                    "INSERT INTO packets VALUES (?, ?, ?, ?)",
                    (row["ts_capture_utc_ns"] - start, station, direction, row["packet_len"]),
                )
            connection.execute("CREATE INDEX ordered ON packets (direction, ts)")
            connection.commit()
            summaries: dict[str, Any] = {}
            for scope in ("global", *station_ids):
                summaries[scope] = {}
                for direction in ("up", "down"):
                    query = "SELECT ts, size FROM packets WHERE direction=?"
                    parameters = (direction,) if scope == "global" else (direction, scope)
                    if scope != "global":
                        query += " AND station=?"
                    cursor = connection.execute(query + " ORDER BY ts, rowid", parameters)
                    summaries[scope][direction] = _summary(iter(cursor), end_s - start_s, policy)
            scratch_bytes = database.stat().st_size
    return dict(
        version=VERSION,
        run_id=manifest["run_id"],
        window_s=[start_s, end_s],
        quantile_method="fixed_histogram_upper_bound",
        summaries=summaries,
        scratch_peak_bytes_lower_bound=scratch_bytes,
    )
