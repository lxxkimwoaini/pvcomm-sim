"""Conservative Oracle audit of serialized IEC 104 commands, never model input.

IEC 104 has no task ID. Each task owns a half-open time interval on its
station/IOA. Ambiguous accept/confirmation/wire evidence fails closed. Feedback
may precede the synchronous transmit() return, but never terminal acceptance.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any


def audit_commands(
    master: list[dict[str, Any]],
    terminal: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    station_id: str,
    common_address: int,
    address: str,
    reachable_power: Callable[[float, dict[str, Any]], float | None] | None = None,
) -> list[dict[str, Any]]:
    own = [r for r in master if r.get("station_id") == station_id]
    commands = sorted(
        (r for r in own if r["event_type"] == "master_command_sent"),
        key=lambda r: r["ts_mono_ns"],
    )
    ids = Counter(r["task_id"] for r in commands)
    used: set[str] = set()
    results = []
    for index, command in enumerate(commands):
        start = command["ts_mono_ns"]
        utc = command["ts_event_utc_ns"]
        following = next((c for c in commands[index + 1 :] if c["ioa"] == command["ioa"]), None)
        ack_end = start + command["deadline_ms"] * 1_000_000
        end = start + command.get("execution_deadline_ms", 10000) * 1_000_000
        if following is not None:
            ack_end = min(ack_end, following["ts_mono_ns"])
            end = min(end, following["ts_mono_ns"])
        utc_ack_end = utc + (ack_end - start)
        target = command["target_kw"]

        def same_value(row: dict[str, Any], field: str, target: float = target) -> bool:
            value = row.get(field)
            return isinstance(value, (int, float)) and abs(value - target) <= 0.1

        def unique(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
            if len(rows) != 1 or str(rows[0][key]) in used:
                return None
            used.add(str(rows[0][key]))
            return rows[0]

        response = unique(
            [
                r
                for r in own
                if r["event_type"] == "master_command_response"
                and r.get("task_id") == command["task_id"]
                and r.get("ioa") == command["ioa"]
                and r.get("positive_confirmation") is True
                and same_value(r, "target_kw")
                and start <= r["ts_mono_ns"] < ack_end
            ],
            "event_id",
        )
        accept = unique(
            [
                r
                for r in terminal
                if r["event_type"] == "terminal_command_accepted"
                and r.get("station_id") == station_id
                and r.get("ioa") == command["ioa"]
                and r.get("common_address") == common_address
                and same_value(r, "target_kw")
                and start <= r["ts_mono_ns"] < ack_end
            ],
            "event_id",
        )
        wire = unique(
            [
                r
                for r in objects
                if r.get("ca") == common_address
                and r.get("ioa") == command["ioa"]
                and r.get("type_id") == 50
                and r.get("cot") == 6
                and r.get("dst_ip") == address
                and not r.get("test_flag")
                and same_value(r, "value_num")
                and utc <= r["available_at"] < utc_ack_end
            ],
            "apdu_id",
        )
        ack = unique(
            [
                r
                for r in objects
                if wire is not None
                and r.get("flow_id") == wire.get("flow_id")
                and r.get("ca") == common_address
                and r.get("ioa") == command["ioa"]
                and r.get("type_id") == 50
                and r.get("cot") == 7
                and r.get("src_ip") == address
                and not r.get("negative_confirm")
                and not r.get("test_flag")
                and same_value(r, "value_num")
                and wire["available_at"] <= r["available_at"] < utc_ack_end
            ],
            "apdu_id",
        )
        feedback: dict[int, dict[str, Any] | None] = {}
        for ioa in (1006, 1001):
            candidates = sorted(
                (
                    r
                    for r in own
                    if accept is not None
                    and r["event_type"] == "master_observed_point"
                    and r.get("ioa") == ioa
                    and (
                        same_value(r, "value")
                        if ioa == 1006 or reachable_power is None
                        else (expected := reachable_power(target, r)) is not None
                        and abs(r["value"] - expected) <= 0.1
                    )
                    and accept["ts_mono_ns"] <= r["ts_mono_ns"] < end
                    and r["event_id"] not in used
                ),
                key=lambda r: r["ts_mono_ns"],
            )
            feedback[ioa] = candidates[0] if candidates else None
            if candidates:
                used.add(candidates[0]["event_id"])
        results.append(
            {
                "task_id": command["task_id"],
                "passed": ids[command["task_id"]] == 1
                and all((response, accept, wire, ack, feedback[1006], feedback[1001])),
                "command_event": command["event_id"],
                "accept_event": accept["event_id"] if accept else None,
                "response_event": response["event_id"] if response else None,
                "wire_apdu": wire["apdu_id"] if wire else None,
                "wire_ack_apdu": ack["apdu_id"] if ack else None,
                "setpoint_feedback_event": feedback[1006]["event_id"] if feedback[1006] else None,
                "power_feedback_event": feedback[1001]["event_id"] if feedback[1001] else None,
                "confirmation_ms": (response["ts_mono_ns"] - start) / 1e6 if response else None,
                "deadline_ms": command["deadline_ms"],
                "execution_end_mono_ns_exclusive": end,
                "method": "serialized_station_ioa_unique_evidence_half_open_intervals",
            }
        )
    return results
