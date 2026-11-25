"""V0.1 acceptance using independent raw PCAP, endpoint callbacks and state.

Oracle evidence is used only here, never exported through the M2 run service.
Checks report failures; they do not relabel or delete difficult observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from pvcomm.config.lab import load_v01_config, v01_config_sha256
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.qc.commands import audit_commands
from pvcomm.qc.pcap import read_apdus
from pvcomm.storage.parquet import read_jsonl, verify_parquet

QC_VERSION = "v01-evidence-v3"
SIGNATURE = (
    "src_ip",
    "dst_ip",
    "src_port",
    "dst_port",
    "packet_index",
    "frame_type",
    "send_seq",
    "recv_seq",
    "type_id",
    "cot",
    "ca",
)


def _p99(values: list[float]) -> float | None:
    return sorted(values)[math.ceil(len(values) * 0.99) - 1] if values else None


def shutdown_capture_tail(row: dict[str, Any], master: list[dict[str, Any]]) -> bool:
    finished = [
        r["ts_event_utc_ns"] for r in master if r["event_type"] == "master_business_finished"
    ]
    stopped = [r["ts_event_utc_ns"] for r in master if r["event_type"] == "master_stopped"]
    return bool(
        finished and stopped and finished[0] <= row["available_at"] <= stopped[0] + 100_000_000
    )


def endpoint_stop_control(
    row: dict[str, Any], address: str, windows: list[tuple[int, int]]
) -> bool:
    return bool(
        row["dst_ip"] == address
        and row["frame_type"] in ("S", "U")
        and any(start <= row["available_at"] < end for start, end in windows)
    )


def _raw_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for row in rows:
        data = bytes.fromhex(row["data_hex"])
        while data:
            if len(data) < 6 or data[0] != 0x68 or len(data) < data[1] + 2:
                raise ValueError("raw callback contains an incomplete APDU")
            size = data[1] + 2
            records.append({**row, "data_hex": data[:size].hex()})
            data = data[size:]
    return records


def evaluate_run(run_dir: Path) -> dict[str, Any]:
    config = load_v01_config(run_dir / "scenario.yaml")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    parsed_dir = run_dir / "parsed" / PARSER_VERSION
    parsed = read_jsonl([parsed_dir / "iec104_apdu.jsonl"])
    objects = read_jsonl([parsed_dir / "iec104_objects.jsonl"])
    master = read_jsonl(list((run_dir / "observed").glob("master_events*.jsonl")))
    terminal = read_jsonl(list((run_dir / "truth").glob("terminal_events*.jsonl")))
    states = read_jsonl(list((run_dir / "truth").glob("electrical_state*.jsonl")))
    raw_master_records = read_jsonl(list((run_dir / "observed").glob("master_apdu_events*.jsonl")))
    raw_master = _raw_messages(raw_master_records)
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, passed: bool, evidence: Any) -> None:
        checks[name] = {"passed": bool(passed), "evidence": evidence}

    captures = manifest.get("captures", [])
    hashes_valid = len(captures) == 1 + len(config.stations)
    independent: dict[str, dict[str, Any]] = {}
    for capture in captures:
        path = run_dir / capture["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes_valid &= actual == capture["sha256"]
        independent[path.stem] = read_apdus(path)
    check("raw_integrity", hashes_valid, {c["path"]: c["sha256"] for c in captures})
    check(
        "isolated_capture_topology",
        manifest.get("capture_topology") == "isolated_master_P0_and_RTU_P1",
        manifest.get("capture_topology"),
    )
    check(
        "completed_and_cleaned",
        manifest["status"] == "completed" and not manifest["cleanup_errors"],
        manifest["status"],
    )
    check(
        "zero_capture_drops",
        len(captures) == 1 + len(config.stations)
        and all(
            c["exit_code"] == 0 and c["statistics"].get("packets_dropped_by_kernel") == 0
            for c in captures
        ),
        {c["path"]: c["statistics"] for c in captures},
    )
    check(
        "configuration_integrity",
        v01_config_sha256(config) == manifest["config_sha256"],
        manifest["config_sha256"],
    )
    p0 = independent.get(
        "primary", {"apdus": [], "complete_apdu_count": 0, "incomplete_byte_count": -1}
    )
    signatures = Counter(tuple(r.get(k) for k in SIGNATURE) for r in p0["apdus"])
    parsed_signatures = Counter(
        tuple(r.get(k) for k in SIGNATURE) for r in parsed if r["parse_status"] == "complete"
    )
    matched = sum((signatures & parsed_signatures).values())
    denominator = p0["complete_apdu_count"]
    rate = matched / denominator if denominator else 0.0
    check(
        "complete_apdu_parse_rate",
        rate >= 0.995
        and sum(parsed_signatures.values()) == denominator
        and p0["incomplete_byte_count"] == 0,
        {
            "confirmed_complete_apdus": denominator,
            "matched": matched,
            "rate": rate,
            "incomplete_bytes": p0["incomplete_byte_count"],
            "unmatched_independent": sum((signatures - parsed_signatures).values()),
            "unexpected_parser": sum((parsed_signatures - signatures).values()),
        },
    )

    addresses = dict(
        zip(manifest["network"]["addresses"], (s.station_id for s in config.stations), strict=True)
    )
    callback_queues: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for row in sorted(raw_master, key=lambda r: r["ts_mono_ns"]):
        callback_queues[(row["station_id"], row["direction"], row["data_hex"])].append(row)
    timing_errors = []
    missing_callbacks = 0
    shutdown_controls = []
    for row in sorted(p0["apdus"], key=lambda r: r["available_at"]):
        station = addresses.get(row["src_ip"], addresses.get(row["dst_ip"]))
        direction = "master_receive" if row["src_ip"] in addresses else "master_send"
        if station is None:
            raise ValueError("captured APDU has no configured station")
        queue = callback_queues[(station, direction, row["data_hex"])]
        if not queue:
            if shutdown_capture_tail(row, master):
                shutdown_controls.append(
                    {"packet_index": row["packet_index"], "data_hex": row["data_hex"]}
                )
            else:
                missing_callbacks += 1
            continue
        callback = queue.popleft()
        timing_errors.append(abs(callback["ts_event_utc_ns"] - row["available_at"]) / 1e6)
    leftovers = sum(len(q) for q in callback_queues.values())
    p99 = _p99(timing_errors)
    check(
        "business_callback_correspondence",
        missing_callbacks == leftovers == 0,
        {
            "matched": len(timing_errors),
            "missing_callbacks": missing_callbacks,
            "extra_callbacks": leftovers,
            "post_business_capture_tail": shutdown_controls,
            "scope": (
                "all application APDUs before master_business_finished; "
                "shutdown tail separately retained"
            ),
        },
    )
    check(
        "log_capture_alignment",
        p99 is not None and p99 <= 100,
        {
            "method": "exact_APDU_bytes_station_direction_FIFO",
            "samples": len(timing_errors),
            "p99_ms": p99,
            "max_ms": max(timing_errors, default=None),
        },
    )

    endpoint_evidence = {}
    for i, station in enumerate(config.stations, start=1):
        address = manifest["network"]["addresses"][i - 1]
        station_p0 = [r for r in p0["apdus"] if address in (r["src_ip"], r["dst_ip"])]
        p1 = independent.get(f"diagnostic_rtu{i}", {"apdus": [], "incomplete_byte_count": -1})

        def fingerprint(r: dict[str, Any]) -> tuple[Any, ...]:
            return (r["src_ip"], r["dst_ip"], r["src_port"], r["dst_port"], r["data_hex"])

        same = Counter(map(fingerprint, station_p0)) == Counter(map(fingerprint, p1["apdus"]))
        raw_terminal = _raw_messages(
            read_jsonl(list((run_dir / "truth").glob(f"terminal_apdu_events_rtu{i}.jsonl")))
        )
        terminal_counts = Counter((r["direction"], r["data_hex"]) for r in raw_terminal)
        p1_counts = Counter(
            ("rtu_send" if r["src_ip"] == address else "rtu_receive", r["data_hex"])
            for r in p1["apdus"]
        )
        # lib60870 can close its application reader while TCP still receives a
        # final S/U control frame. Retain and report only missing controls whose
        # PCAP time is strictly inside an explicitly logged server.stop call.
        # I frames, sends, extra callbacks, and controls outside it still fail.
        lifecycle = sorted(
            (
                r
                for r in terminal
                if r.get("station_id") == station.station_id
                and r["event_type"] in ("rtu_stopping", "rtu_stopped")
            ),
            key=lambda r: r["ts_mono_ns"],
        )
        stop_windows = [
            (a["ts_event_utc_ns"], b["ts_event_utc_ns"])
            for a, b in zip(lifecycle, lifecycle[1:], strict=False)
            if a["event_type"] == "rtu_stopping" and b["event_type"] == "rtu_stopped"
        ]
        missing = p1_counts - terminal_counts
        lifecycle_controls = []
        for row in p1["apdus"]:
            key = ("rtu_send" if row["src_ip"] == address else "rtu_receive", row["data_hex"])
            if missing[key] and endpoint_stop_control(row, address, stop_windows):
                missing[key] -= 1
                lifecycle_controls.append(
                    {
                        "packet_index": row["packet_index"],
                        "available_at": row["available_at"],
                        "data_hex": row["data_hex"],
                    }
                )
        endpoint_evidence[station.station_id] = {
            "p0_apdus": len(station_p0),
            "p1_apdus": len(p1["apdus"]),
            "same_bytes": same,
            "terminal_callback_match": not +missing and not +(terminal_counts - p1_counts),
            "lifecycle_transport_controls": lifecycle_controls,
            "incomplete_bytes": p1["incomplete_byte_count"],
        }
    check(
        "dual_endpoint_correspondence",
        all(
            r["same_bytes"] and r["terminal_callback_match"] and r["incomplete_bytes"] == 0
            for r in endpoint_evidence.values()
        ),
        endpoint_evidence,
    )

    expected_commands = 2
    if (run_dir / "power_config.json").exists():
        from pvcomm.config.power import power_document

        power_config = power_document(run_dir / "power_config.json")
        expected_commands += int(power_config.repeat_command_after_s is not None)
    business = {}
    for station in config.stations:
        observations = [r for r in master if r.get("station_id") == station.station_id]
        seen = {r["ioa"] for r in observations if r["event_type"] == "master_observed_point"}
        gi = {r["cot"] for r in parsed if r["type_id"] == 100 and r["ca"] == station.common_address}
        commands = [r for r in observations if r["event_type"] == "master_command_sent"]
        accepted = [
            r
            for r in terminal
            if r["event_type"] == "terminal_command_accepted"
            and r["station_id"] == station.station_id
        ]

        def reachable_power(target: float, row: dict[str, Any]) -> float | None:
            physical = [
                s
                for s in states
                if s.get("source") == "pvlib_pvwatts_inverter_v02"
                and s["station_id"] == row["station_id"]
                and s["model_mono_ns"] <= row["ts_mono_ns"]
            ]
            return (
                min(target, max(physical, key=lambda sample: sample["model_mono_ns"])["p_avail_kw"])
                if physical
                else None
            )

        targets = audit_commands(
            master,
            terminal,
            objects,
            station.station_id,
            station.common_address,
            manifest["network"]["addresses"][config.stations.index(station)],
            reachable_power if (run_dir / "power_config.json").exists() else None,
        )
        business[station.station_id] = {
            "telemetry_ioas": sorted(seen),
            "gi_cots": sorted(gi),
            "accepted_count": len(accepted),
            "commands": targets,
            "passed": {1001, 1002, 1003, 1006, 2001}.issubset(seen)
            and {6, 7, 10}.issubset(gi)
            and len(commands) == len(accepted) == expected_commands
            and all(t["passed"] for t in targets),
        }
    check("three_station_business_loop", all(r["passed"] for r in business.values()), business)
    bounds = all(
        0
        <= r["p_actual_kw"]
        <= next(s.capacity_kw for s in config.stations if s.station_id == r["station_id"])
        and r["p_actual_kw"] <= r["p_avail_kw"] + 0.0001
        for r in states
    )
    check(
        "protocol_model_power_bounds",
        bool(states) and bounds,
        {
            "samples": len(states),
            "physical_model": "pvlib_v02"
            if (run_dir / "power_config.json").exists()
            else "fixed_available_power_ramp_test_only",
        },
    )
    ids = [r["event_id"] for r in [*master, *terminal, *states, *raw_master_records]]
    check(
        "event_identity_and_causality",
        len(ids) == len(set(ids))
        and all(r["available_at"] == r["ts_event_utc_ns"] for r in master),
        {"events": len(ids)},
    )
    start = [r["ts_mono_ns"] for r in master if r["event_type"] == "master_business_started"]
    end = [r["ts_mono_ns"] for r in master if r["event_type"] == "master_business_finished"]
    duration = (end[0] - start[0]) / 1e9 if len(start) == len(end) == 1 else None
    check(
        "configured_real_duration",
        duration is not None and duration >= config.duration_s - 0.1,
        duration,
    )
    parquet_evidence = verify_parquet(run_dir)
    check("parquet_export", parquet_evidence["passed"], parquet_evidence)
    event = config.interruption
    interruption_evidence: dict[str, Any] | None = None
    if event is not None:
        suspended = [r for r in terminal if r["event_type"] == "terminal_suspended"]
        resumed = [r for r in terminal if r["event_type"] == "terminal_resumed"]
        plans = [r for r in master if r["event_type"] == "master_maintenance_plan"]
        closed = [
            r
            for r in master
            if r["event_type"] == "master_connection_state"
            and r["station_id"] == event.station_id
            and r["state"].startswith("CLOSED")
            and suspended
            and r["ts_event_utc_ns"] >= suspended[0]["ts_event_utc_ns"] - 1_000_000_000
        ]
        recovery = [
            r for r in master if r["event_type"] == "master_recovery_interrogation" and r["success"]
        ]
        restored = [
            r
            for r in master
            if r["event_type"] == "master_observed_point"
            and r.get("ioa") == 1001
            and r["station_id"] == event.station_id
            and resumed
            and r["ts_event_utc_ns"] > resumed[0]["ts_event_utc_ns"]
        ]
        legal = bool(
            plans
            and suspended
            and plans[0]["available_at"] < suspended[0]["ts_event_utc_ns"]
            and resumed
            and resumed[0]["ts_event_utc_ns"] <= plans[0]["window_end_utc_ns"] + 15_000_000_000
        )
        interruption_evidence = {
            "fault_applied": not event.planned and bool(suspended),
            "transport_impact": bool(closed),
            "service_violation": bool(closed) and not legal,
            "observable_anomaly": bool(closed) and not legal,
            "operating_context": "maintenance" if legal else "normal_operation",
            "label_status": "confirmed" if closed and restored else "uncertain",
            "scope": [event.station_id],
            "online_decidable_at": closed[0]["available_at"] if closed else None,
            "evidence_event_ids": [
                r["event_id"] for r in [*suspended, *resumed, *closed, *recovery]
            ],
            "recovered": bool(restored and recovery),
            "development_audit_only": True,
        }
        check(
            "interruption_and_recovery",
            bool(suspended and resumed and closed and restored and recovery)
            and (legal == event.planned),
            interruption_evidence,
        )
    return {
        "qc_version": QC_VERSION,
        "run_id": config.run_id,
        "code_version": manifest["code_version"],
        "run_code_dirty": manifest["code_dirty"],
        "checks": checks,
        "passed": all(c["passed"] for c in checks.values()),
        "business_duration_s": duration,
        "normal_30min_candidate": event is None and duration is not None and duration >= 1799.9,
        "interruption_audit": interruption_evidence,
    }


def qc_run(run_dir: Path) -> dict[str, Any]:
    destination = run_dir / "qc" / QC_VERSION
    if destination.exists():
        raise FileExistsError(f"QC output exists: {destination}")
    report = evaluate_run(run_dir)
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".qc-", dir=destination.parent) as temp:
        stage = Path(temp) / "report"
        stage.mkdir()
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return report
