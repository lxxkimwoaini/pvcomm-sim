"""Station causal windows from P0 and observed master logs only.

Oracle labels are joined after feature construction through a separate code path.
SQLite is disposable indexed working storage; original evidence is never edited.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sqlite3
import statistics
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.domain.labels import EventLabel, ServiceFinding
from pvcomm.pipeline.dataset import code_record, read_json, write_json
from pvcomm.storage.parquet import iter_jsonl, source_hash

M0 = (
    "up_packets",
    "down_packets",
    "up_bytes",
    "down_bytes",
    "payload_bytes",
    "packet_length_mean",
    "packet_length_std",
    "iat_mean_s",
    "iat_p95_s",
    "packet_rate",
    "retransmission_fraction",
    "burst_fraction",
    "active_span_s",
)
M1 = (
    "i_frames",
    "s_frames",
    "u_frames",
    "negative_confirmations",
    "commands",
    "interrogations",
    "clock_syncs",
    "telemetry_objects",
    "invalid_quality_objects",
)
M2 = (
    "power_kw",
    "available_kw",
    "voltage_kv",
    "power_age_s",
    "available_age_s",
    "power_quality_known",
    "power_age_budget_ratio",
    "fresh_point_coverage",
    "last_target_kw",
    "command_age_s",
    "confirmation_wait_s",
    "execution_wait_s",
    "known_maintenance_active",
    "maintenance_recovery_remaining_s",
    "peer_fresh_power_coverage",
    "peer_mean_power_kw",
    "peer_mean_power_age_s",
)
FEATURES = {"M0": M0, "M1": M0 + M1, "M2": M0 + M1 + M2}
REQUIRED_IOAS = (1001, 1002, 1003, 1008, 2001)
MASTER_TYPES = {
    "master_observed_point",
    "master_command_sent",
    "master_command_confirmation",
    "master_maintenance_plan",
    "master_business_started",
    "master_business_finished",
}


def finite_number(value: object) -> float | None:
    if isinstance(value, (float, int)) and math.isfinite(value):
        return float(value)
    return None


class Observations:
    def __init__(self, database: Path) -> None:
        self.connection = sqlite3.connect(database)
        self.connection.execute("CREATE TABLE r(kind TEXT, station TEXT, ts INTEGER, detail TEXT)")

    def close(self) -> None:
        self.connection.close()

    def load(self, root: Path, record: dict[str, Any]) -> dict[str, Any]:
        config = read_json(root / "power_config.json")
        stations = config["protocol"]["stations"]
        manifest = read_json(root / "manifest.json")
        addresses = {
            ip: s["station_id"]
            for ip, s in zip(manifest["network"]["addresses"], stations, strict=True)
        }
        for name, kind in (
            ("packets", "packet"),
            ("iec104_apdu", "apdu"),
            ("iec104_objects", "object"),
        ):
            relative = f"parsed/tshark-pdml-v2/{name}.jsonl"
            for row in iter_jsonl([root / relative]):
                if row["run_id"] != record["run_id"]:
                    raise ValueError("cross-run protocol evidence")
                if row["capture_id"] != "primary":
                    raise ValueError("feature input must be exclusively primary P0")
                station = addresses.get(row.get("src_ip")) or addresses.get(row.get("dst_ip"))
                if station is None:
                    continue
                row["up"] = row.get("src_ip") in addresses
                ts = max(row["ts_capture_utc_ns"], row.get("available_at") or 0)
                self.insert(kind, station, ts, row)
        for row in iter_jsonl(sorted((root / "observed").glob("master_events*.jsonl"))):
            if row["run_id"] != record["run_id"]:
                raise ValueError("cross-run master observation")
            if row["event_type"] in MASTER_TYPES:
                ts = max(row["available_at"], row["ts_event_utc_ns"])
                self.insert("master", row.get("station_id", ""), ts, row)
        self.connection.execute("CREATE INDEX query_r ON r(kind,station,ts)")
        self.connection.execute("CREATE INDEX availability_r ON r(ts)")
        self.connection.execute(
            "CREATE INDEX context_r ON r(station,ts) WHERE kind='master' "
            "AND json_extract(detail,'$.event_type')!='master_observed_point'"
        )
        self.connection.execute(
            "CREATE INDEX point_r ON r(station,json_extract(detail,'$.ioa'),ts DESC) "
            "WHERE kind='master' AND json_extract(detail,'$.event_type')='master_observed_point'"
        )
        self.connection.commit()
        return config

    def insert(self, kind: str, station: str, timestamp: int, row: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO r VALUES(?,?,?,?)",
            (
                kind,
                station,
                timestamp,
                json.dumps(row, separators=(",", ":")),
            ),
        )

    def rows(self, kind: str, station: str, start: int, end: int) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT detail FROM r WHERE kind=? AND station=? AND ts>=? AND ts<? ORDER BY ts",
                (kind, station, start, end),
            )
        ]

    def master(self, station: str, end: int) -> list[dict[str, Any]]:
        # Keep only state-changing context across the whole history. Point values
        # are retrieved through indexed last-value queries instead of full scans.
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT detail FROM r WHERE kind='master' AND station=? AND ts<? "
                "AND json_extract(detail,'$.event_type')!='master_observed_point' ORDER BY ts",
                (station, end),
            )
        ]

    def latest_point(self, station: str, ioa: int, end: int) -> dict[str, Any] | None:
        result = self.connection.execute(
            "SELECT detail FROM r WHERE kind='master' AND station=? AND ts<? "
            "AND json_extract(detail,'$.event_type')='master_observed_point' "
            "AND json_extract(detail,'$.ioa')=? ORDER BY ts DESC LIMIT 1",
            (station, end, ioa),
        ).fetchone()
        return json.loads(result[0]) if result else None

    def latest_available(self, end: int) -> int | None:
        value: int | None = self.connection.execute(
            "SELECT max(ts) FROM r WHERE ts<?", (end,)
        ).fetchone()[0]
        return value


def percentile(values: list[float], fraction: float) -> float | None:
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def traffic_features(db: Observations, station: str, start: int, end: int) -> dict[str, Any]:
    packets = db.rows("packet", station, start, end)
    apdus = db.rows("apdu", station, start, end)
    objects = db.rows("object", station, start, end)
    times = [row["ts_capture_utc_ns"] for row in packets]
    lengths = [float(row["packet_len"]) for row in packets]
    intervals = [(b - a) / 1e9 for a, b in zip(times, times[1:], strict=False)]
    seconds = (end - start) / 1e9
    result: dict[str, Any] = {}
    for direction in ("up", "down"):
        members = [r for r in packets if r["up"] is (direction == "up")]
        result[f"{direction}_packets"] = len(members)
        result[f"{direction}_bytes"] = sum(r["packet_len"] for r in members)
    result.update(
        payload_bytes=sum(r["tcp_payload_len"] for r in packets),
        packet_length_mean=statistics.mean(lengths) if lengths else None,
        packet_length_std=statistics.pstdev(lengths) if lengths else None,
        iat_mean_s=statistics.mean(intervals) if intervals else None,
        iat_p95_s=percentile(intervals, 0.95),
        packet_rate=len(packets) / seconds,
        retransmission_fraction=sum(bool(r["tcp_retransmission"]) for r in packets) / len(packets)
        if packets
        else 0.0,
        burst_fraction=sum(t < 0.01 for t in intervals) / len(intervals) if intervals else 0.0,
        active_span_s=(max(times) - min(times)) / 1e9 if times else 0.0,
        negative_confirmations=sum(bool(r.get("negative_confirm")) for r in apdus),
        commands=sum(r.get("type_id") == 50 and r.get("cot") == 6 for r in apdus),
        interrogations=sum(r.get("type_id") == 100 and r.get("cot") == 6 for r in apdus),
        clock_syncs=sum(r.get("type_id") == 103 for r in apdus),
        telemetry_objects=sum(r.get("type_id") in (1, 13, 30, 36) for r in objects),
        invalid_quality_objects=sum(bool(r.get("quality_raw")) for r in objects),
    )
    for frame in ("I", "S", "U"):
        result[f"{frame.lower()}_frames"] = sum(r["frame_type"] == frame for r in apdus)
    return result


def business_features(
    db: Observations,
    station: dict[str, Any],
    end: int,
) -> tuple[dict[str, Any], bool]:
    identity = station["station_id"]
    budget = station["telemetry_period_s"] * 2 + 1
    points = {ioa: db.latest_point(identity, ioa, end) for ioa in (*REQUIRED_IOAS, 1006)}
    history = db.master(identity, end)
    plans = [
        r
        for r in history
        if r["event_type"] == "master_maintenance_plan"
        and r["available_at"] <= r["window_start_utc_ns"]
    ]
    active_plan = next(
        (
            r
            for r in reversed(plans)
            if r["window_start_utc_ns"] <= end - 1 < r["window_end_utc_ns"]
        ),
        None,
    )
    grace_plan = next(
        (
            r
            for r in reversed(plans)
            if r["window_start_utc_ns"]
            <= end - 1
            < r["window_end_utc_ns"] + int(r["recovery_deadline_s"] * 1e9)
        ),
        None,
    )
    ages: dict[int, float | None] = {}
    values: dict[int, float | None] = {}
    for ioa, point in points.items():
        ages[ioa] = (end - point["available_at"]) / 1e9 if point else None
        values[ioa] = (
            finite_number(point.get("value"))
            if point and point.get("quality_valid") is True
            else None
        )
    fresh = [
        ioa
        for ioa in REQUIRED_IOAS
        if values[ioa] is not None and ages[ioa] is not None and ages[ioa] <= budget
    ]
    features: dict[str, Any] = dict(
        power_kw=values[1001],
        available_kw=values[1008],
        voltage_kv=values[1003],
        power_age_s=ages[1001],
        available_age_s=ages[1008],
        power_quality_known=float(values[1001] is not None),
        power_age_budget_ratio=ages[1001] / budget if ages[1001] is not None else None,
        fresh_point_coverage=len(fresh) / len(REQUIRED_IOAS),
        last_target_kw=None,
        command_age_s=None,
        confirmation_wait_s=0.0,
        execution_wait_s=0.0,
        known_maintenance_active=float(active_plan is not None),
        maintenance_recovery_remaining_s=(
            (grace_plan["window_end_utc_ns"] + int(grace_plan["recovery_deadline_s"] * 1e9) - end)
            / 1e9
            if grace_plan
            else 0.0
        ),
    )
    # This baseline is independently computed from online observations; it does
    # not read final findings or simulator outcomes, and is never a model column.
    violation = len(fresh) < len(REQUIRED_IOAS) and grace_plan is None
    commands = [r for r in history if r["event_type"] == "master_command_sent"]
    if commands:
        command = commands[-1]  # Later same-point task supersedes attribution.
        age = (end - command["sent_utc_ns"]) / 1e9
        features.update(last_target_kw=command["target_kw"], command_age_s=age)
        confirmations = [
            r
            for r in history
            if r["event_type"] == "master_command_confirmation"
            and r.get("matched") is True
            and r.get("task_id") == command["task_id"]
            and r.get("connection_epoch") == command.get("connection_epoch")
        ]
        # The live tracker expires at its deadline, so a genuine late reply may
        # have matched=False. Resolve it only when this epoch/point/value has a
        # single causal command; a repeated setpoint must remain ambiguous.
        if not confirmations:
            same_value_commands = [
                r
                for r in commands
                if r.get("connection_epoch") == command.get("connection_epoch")
                and r.get("ioa") == command.get("ioa")
                and abs(r["target_kw"] - command["target_kw"]) <= 0.1
            ]
            if len(same_value_commands) == 1:
                late = [
                    r
                    for r in history
                    if r["event_type"] == "master_command_confirmation"
                    and r.get("matched") is False
                    and r.get("connection_epoch") == command.get("connection_epoch")
                    and r.get("ioa") == command.get("ioa")
                    and abs(r.get("target_kw", math.inf) - command["target_kw"]) <= 0.1
                    and r["available_at"] >= command["sent_utc_ns"]
                ]
                if len(late) == 1:
                    confirmations = late
        if not confirmations:
            features["confirmation_wait_s"] = age
            violation |= age > command["deadline_ms"] / 1000
        elif confirmations[-1]["positive_confirmation"] is True:
            feedback = (
                all(
                    points[ioa] is not None
                    and values[ioa] is not None
                    and (points[ioa] or {}).get("available_at", -1) >= command["sent_utc_ns"]
                    for ioa in (1001, 1006)
                )
                and values[1006] is not None
                and abs(values[1006] - command["target_kw"])
                <= max(0.1, station["capacity_kw"] * 0.01)
            )
            if not feedback:
                features["execution_wait_s"] = age
                violation |= age > command["execution_deadline_ms"] / 1000
    return features, bool(violation)


def window_label(findings: Sequence[ServiceFinding], start: int, end: int) -> dict[str, Any]:
    """Union overlap, t-1ns state, censored suffix and supersession are separate."""
    intervals = []
    observable_intervals = []
    unknown = False
    ids = []
    for finding in findings:
        left = finding.start_utc_ns
        right = finding.end_utc_ns if finding.end_utc_ns is not None else end
        if left < end and start < right:
            if finding.label_status != "valid" or finding.service_violation is None:
                unknown = True
            elif finding.service_violation:
                intervals.append((max(left, start), min(right, end)))
                observable_intervals.append(
                    (max(left, finding.observable_at_utc_ns or left), right)
                )
                ids.append(finding.finding_id)
    total = 0
    boundary = start
    for left, right in sorted(intervals):
        total += max(0, right - max(left, boundary))
        boundary = max(boundary, right)
    at_end = any(a <= end - 1 < b for a, b in intervals)
    return dict(
        label_status="uncertain" if unknown else "valid",
        label_at_end=None if unknown else int(at_end),
        label_any=None if unknown else int(bool(intervals)),
        observable_at_end=None
        if unknown
        else int(any(a <= end - 1 < b for a, b in observable_intervals)),
        anomaly_overlap_ratio=None if unknown else total / (end - start),
        finding_ids=ids,
    )


def sample_windows(
    db: Observations,
    record: dict[str, Any],
    config: dict[str, Any],
    policy: DatasetPolicy,
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]]:
    root = Path(record["path"])
    labels = {
        r["station_id"]: EventLabel.model_validate(r)
        for r in iter_jsonl([root / "labels/v04-label-v3/event_labels.jsonl"])
    }
    origin = record["scene_clock"]["start_utc_ns"]
    warmup = int(config["protocol"]["warmup_s"] * 1e9)
    end_run = origin + int(record["duration_s"] * 1e9)
    finished = db.rows("master", "", 0, end_run + 2_000_000_000)
    actual_end = [
        r["available_at"] for r in finished if r["event_type"] == "master_business_finished"
    ]
    if len(actual_end) != 1:
        raise ValueError("missing or ambiguous actual business end")
    end_run = min(end_run, actual_end[0])
    first = origin + warmup + policy.window_s * 10**9
    stations = config["protocol"]["stations"]
    for end in range(first, end_run + 1, policy.step_s * 10**9):
        start = end - policy.window_s * 10**9
        latest_available = db.latest_available(end)
        business = {s["station_id"]: business_features(db, s, end) for s in stations}
        for station in stations:
            identity = station["station_id"]
            identity_text = f"{record['run_id']}:{identity}:{end}"
            if policy.schema_version == "0.5.1-dataset.1":
                identity_text += f":{policy.window_s}:{policy.step_s}"
            sample_id = hashlib.sha256(identity_text.encode()).hexdigest()
            row = traffic_features(db, identity, start, end)
            own, rule = business[identity]
            row.update(own)
            peers = [business[s["station_id"]][0] for s in stations if s != station]
            fresh_peers = [
                p
                for p in peers
                if p["power_kw"] is not None
                and p["power_age_s"] is not None
                and p["power_age_budget_ratio"] <= 1
            ]
            row.update(
                peer_fresh_power_coverage=len(fresh_peers) / len(peers),
                peer_mean_power_kw=statistics.mean(p["power_kw"] for p in fresh_peers)
                if fresh_peers
                else None,
                peer_mean_power_age_s=statistics.mean(p["power_age_s"] for p in fresh_peers)
                if fresh_peers
                else None,
            )
            if set(row) != set(FEATURES["M2"]):
                raise ValueError("feature whitelist mismatch")
            index = dict(
                sample_id=sample_id,
                run_id=record["run_id"],
                station_id=identity,
                window_start_utc_ns=start,
                window_end_utc_ns=end,
                scene_end_s=(end - origin) / 1e9,
                latest_input_available_at=latest_available,
            )
            yield (
                dict(sample_id=sample_id, **row),
                dict(sample_id=sample_id, **window_label(labels[identity].findings, start, end)),
                dict(sample_id=sample_id, business_rule=int(rule)),
                index,
            )


def build_features(root: Path, mode: str = "M2", scale: str | None = None) -> dict[str, Any]:
    if mode not in FEATURES:
        raise ValueError("supported observation modes: M0/M1/M2; M3 has no independent source")
    output_root = root / scale if scale else root
    destination = output_root / f"features-{mode}"
    if destination.exists():
        raise FileExistsError("feature export already exists")
    split = read_json(root / "split/assignment.json")
    audit = read_json(root / "split/audit.json")
    if (
        not audit["passed"]
        or source_hash(root / "split/assignment.json") != audit["assignment_sha256"]
        or source_hash(root / "inventory.json") != audit["inventory_sha256"]
    ):
        raise ValueError("frozen split integrity failed")
    policy = DatasetPolicy.model_validate(read_json(root / "split/policy.json"))
    if policy.schema_version == "0.5.1-dataset.1" and scale is None:
        raise ValueError("V0.5.1 requires an explicit registered scale")
    if scale:
        policy = policy.for_scale(scale)
    if source_hash(root / "runs.jsonl") != read_json(root / "inventory.json")["run_index_sha256"]:
        raise ValueError("run index changed after split")
    catalogue = {r["run_id"]: r for r in iter_jsonl([root / "runs.jsonl"]) if r["eligible"]}
    output_root.mkdir(exist_ok=True)
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    feature_schema = pa.schema(
        [("sample_id", pa.string()), *((name, pa.float64()) for name in FEATURES[mode])],
        metadata={
            b"scope": mode.encode(),
            b"contract": (b"0.5.1-features.1" if scale else b"0.5-features.1"),
        },
    )
    label_schema = pa.schema(
        [
            ("sample_id", pa.string()),
            ("label_status", pa.string()),
            ("label_at_end", pa.int8()),
            ("label_any", pa.int8()),
            ("observable_at_end", pa.int8()),
            ("anomaly_overlap_ratio", pa.float64()),
            ("finding_ids", pa.list_(pa.string())),
        ],
        metadata={b"scope": b"Oracle_labels_only"},
    )
    rule_schema = pa.schema([("sample_id", pa.string()), ("business_rule", pa.int8())])
    index_schema = pa.schema(
        [
            ("sample_id", pa.string()),
            ("run_id", pa.string()),
            ("station_id", pa.string()),
            ("window_start_utc_ns", pa.int64()),
            ("window_end_utc_ns", pa.int64()),
            ("scene_end_s", pa.float64()),
            ("partition", pa.string()),
            ("group_id", pa.string()),
            ("latest_input_available_at", pa.int64()),
        ]
    )
    count = 0
    positive = 0
    causality_checked = 0
    with tempfile.TemporaryDirectory(prefix=".features-", dir=root) as temporary:
        stage = Path(temporary) / "tables"
        stage.mkdir()
        schemas = dict(
            features=feature_schema, labels=label_schema, rules=rule_schema, index=index_schema
        )
        writers = {
            name: pq.ParquetWriter(stage / f"{name}.parquet", schema, compression="zstd")
            for name, schema in schemas.items()
        }
        try:
            for assignment in split["runs"]:
                record = catalogue[assignment["run_id"]]
                if assignment["group_keys"] != record["group_keys"]:
                    raise ValueError("source grouping changed after split")
                run = Path(record["path"])
                for relative, expected in record["evidence_sha256"].items():
                    if source_hash(run / relative) != expected:
                        raise ValueError(f"evidence changed since inventory: {relative}")
                db = Observations(Path(temporary) / "working.sqlite")
                try:
                    config = db.load(run, record)
                    buffers: dict[str, list[dict[str, Any]]] = {name: [] for name in schemas}
                    for feature, label, rule, index in sample_windows(db, record, config, policy):
                        latest = index["latest_input_available_at"]
                        if latest is not None and latest >= index["window_end_utc_ns"]:
                            raise ValueError("future observation in causal window")
                        causality_checked += 1
                        buffers["features"].append(
                            {k: feature[k] for k in ("sample_id", *FEATURES[mode])}
                        )
                        buffers["labels"].append(label)
                        buffers["rules"].append(rule)
                        buffers["index"].append(
                            dict(
                                index,
                                partition=assignment["partition"],
                                group_id=assignment["group_id"],
                            )
                        )
                        count += 1
                        positive += label["label_at_end"] == 1
                        if len(buffers["index"]) >= 512:
                            for name, schema in schemas.items():
                                writers[name].write_table(
                                    pa.Table.from_pylist(buffers[name], schema=schema)
                                )
                                buffers[name].clear()
                    for name, schema in schemas.items():
                        if buffers[name]:
                            writers[name].write_table(
                                pa.Table.from_pylist(buffers[name], schema=schema)
                            )
                finally:
                    db.close()
                    (Path(temporary) / "working.sqlite").unlink()
        finally:
            for writer in writers.values():
                writer.close()
        write_json(
            stage / "whitelist.json",
            dict(mode=mode, features=FEATURES[mode], cumulative_modes=FEATURES),
        )
        report = dict(
            passed=True,
            rows=count,
            positive_at_end=positive,
            mode=mode,
            window_s=policy.window_s,
            step_s=policy.step_s,
            label_convention=f"state_at_t_minus_1ns; union overlap on [t-{policy.window_s}s,t)",
            inputs="primary P0 + observed/master_events only; labels separate",
            future_rows_excluded=True,
            oracle_feature_columns=[],
            causality_checked_samples=causality_checked,
            split_sha256=audit["assignment_sha256"],
            tables={name: source_hash(stage / f"{name}.parquet") for name in schemas},
            **code_record(),
        )
        write_json(stage / "quality.json", report)
        stage.rename(destination)
    return report
