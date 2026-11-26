"""Fail-closed resolution of pre-write TESTFR attempts, never wire events or labels."""

import hashlib
import json
import re
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path
from typing import Any

from pvcomm.capture.socket_audit import VERSION
from pvcomm.qc.pcap import read_apdus
from pvcomm.qc.v01 import _raw_messages
from pvcomm.storage.parquet import read_jsonl

TESTFR_ACT = "680443000000"
PREFIX = re.compile(r"^(\d+\.\d+) (sendto|shutdown|close)\((\d+)<TCP:\[([^\]]+)\]>(.*)$")
SEND = re.compile(r'^, "((?:\\x[0-9a-f]{2})+)", (\d+), [^,]+, NULL, 0\) = (-?\d+)(?: (\w+).*)?$')
RETURN = re.compile(r"^\)?(?:, SHUT_RDWR\))? = (-?\d+)(?: (\w+).*)?$")


def read_socket_calls(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    contract = manifest["socket_audit"]
    expected = {"master", *(f"rtu{i}" for i in range(1, len(manifest["network"]["rtus"]) + 1))}
    if not (
        contract["version"] == VERSION
        and contract["scope"] == "Oracle"
        and contract["completed"] is True
        and set(contract["workers"]) == expected
        and manifest["dependencies"]["c104"] == "2.2.1"
    ):
        raise ValueError("socket audit contract or worker identities disagree")
    paths = []
    calls = []
    for item in contract["files"]:
        path = root / item["path"]
        role = item["role"]
        if (
            role not in expected
            or path.parent.resolve() != (root / "truth/socket_audit").resolve()
            or not re.fullmatch(re.escape(role) + r"\.[1-9][0-9]*", path.name)
            or path.is_symlink()
        ):
            raise ValueError("socket audit path is outside its owned worker evidence")
        content = path.read_bytes()
        lines = content.decode("utf-8").splitlines()
        if (
            hashlib.sha256(content).hexdigest() != item["sha256"]
            or not lines
            or "+++ exited with 0 +++" not in lines[-1]
            or item["trace_exit_code"] != 0
        ):
            raise ValueError("socket audit hash or completion disagrees")
        paths.append(path.resolve())
        for index, line in enumerate(lines, 1):
            match = PREFIX.fullmatch(line)
            if match is None:
                if "sendto(" in line or "<... sendto resumed>" in line:
                    raise ValueError("unsupported or incomplete socket send evidence")
                continue
            timestamp, operation, fd, socket, remainder = match.groups()
            row: dict[str, Any] = {
                "role": role,
                "ts_utc_ns": int(Decimal(timestamp) * 1_000_000_000),
                "operation": operation,
                "fd": int(fd),
                "socket": socket,
                "evidence_ref": f"{item['path']}:{index}",
            }
            result = (
                SEND.fullmatch(remainder) if operation == "sendto" else RETURN.fullmatch(remainder)
            )
            if result is None:
                raise ValueError("unsupported TCP syscall evidence")
            if operation == "sendto":
                escaped, size, returned, error = result.groups()
                row.update(data_hex=escaped.replace("\\x", ""), size=int(size))
                if len(row["data_hex"]) != int(size) * 2:
                    raise ValueError("truncated TCP syscall payload")
            else:
                returned, error = result.groups()
            row.update(returned=int(returned), error=error)
            calls.append(row)
    actual = {p.resolve() for p in (root / "truth/socket_audit").glob("*") if p.is_file()}
    if len(paths) != len(set(paths)) or set(paths) != actual:
        raise ValueError("socket audit file inventory disagrees")
    for role, worker in contract["workers"].items():
        if (root / f"truth/socket_audit/{role}.{worker['worker_pid']}").resolve() not in actual:
            raise ValueError("socket audit omits an owned main worker")
        if not any(c["role"] == role and c["operation"] == "sendto" for c in calls):
            raise ValueError("socket audit omits owned worker TCP sends")
    return sorted(calls, key=lambda r: (r["ts_utc_ns"], r["evidence_ref"]))


def account_socket_quality(experiment: dict[str, Any], socket_evidence: dict[str, Any]) -> None:
    """A01-only accounting; retain the strict result and all numerical checks."""
    quality = experiment["data_quality"]["checks"]
    strict_callbacks = quality["business_callback_correspondence"]
    original = strict_callbacks["evidence"]
    if (
        socket_evidence["passed"]
        and original["missing_callbacks"] == 0
        and original["extra_callbacks"] == socket_evidence["count"]
    ):
        quality["business_callback_correspondence"] = {
            "passed": True,
            "strict_normal_comparison": strict_callbacks,
            "socket_attempt_resolution": socket_evidence,
        }
    quality["a01_kernel_socket_evidence"] = socket_evidence
    experiment["qc_version"] += "+A01-kernel-socket-evidence.1"
    experiment["data_quality"]["passed"] = all(c["passed"] for c in quality.values())
    experiment["passed"] = (
        experiment["run_integrity"]["passed"] and experiment["data_quality"]["passed"]
    )


def resolve_test_attempts(
    extras: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    addresses: dict[str, str],
    start: int,
    end: int,
) -> dict[str, Any]:
    resolved: list[dict[str, Any]] = []
    used = set()
    for row in extras:
        candidates = [
            c
            for c in calls
            if c["role"] == "master"
            and c["operation"] == "sendto"
            and c["data_hex"] == TESTFR_ACT
            and c["size"] == c["returned"] == 6
            and c["evidence_ref"] not in used
            and c["socket"].endswith(f"->{addresses.get(row['station_id'])}:2404")
            and start <= c["ts_utc_ns"] < end
            and abs(c["ts_utc_ns"] - row["ts_event_utc_ns"]) <= 100_000_000
        ]
        if (
            row["direction"] != "master_send"
            or row["data_hex"] != TESTFR_ACT
            or not start <= row["ts_event_utc_ns"] < end
            or len(candidates) != 1
        ):
            return {"passed": False, "resolved": resolved, "unresolved_event": row["event_id"]}
        send = candidates[0]
        following = [
            c
            for c in calls
            if c["role"] == "master"
            and c["fd"] == send["fd"]
            and send["ts_utc_ns"] < c["ts_utc_ns"] < end
        ]
        closed = next((c for c in following if c["operation"] == "close"), None)
        disconnected = next(
            (c for c in following if c["operation"] == "shutdown" and c["error"] == "ENOTCONN"),
            None,
        )
        if not (
            closed
            and closed["returned"] == 0
            and disconnected
            and disconnected["returned"] == -1
            and disconnected["ts_utc_ns"] < closed["ts_utc_ns"]
        ):
            return {"passed": False, "resolved": resolved, "unresolved_event": row["event_id"]}
        used.add(send["evidence_ref"])
        resolved.append(
            {
                "raw_event_id": row["event_id"],
                "send": send,
                "disconnected": disconnected,
                "closed": closed,
                "classification": "kernel_accepted_TESTFR_attempt_not_observed_on_interface",
            }
        )
    return {"passed": True, "resolved": resolved, "count": len(resolved)}


def audit_unobserved_tests(
    root: Path, events: list[dict[str, Any]], targets: tuple[str, ...]
) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_bytes())
    try:
        calls = read_socket_calls(root, manifest)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return {"passed": False, "error": str(exc)}
    queues: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for row in _raw_messages(
        read_jsonl(list((root / "observed").glob("master_apdu_events*.jsonl")))
    ):
        queues[(row["station_id"], row["direction"], row["data_hex"])].append(row)
    config = json.loads((root / "effective_config.json").read_bytes())
    stations = config["stations"]
    addresses = {s["address"]: s["station_id"] for s in stations}
    for row in read_apdus(root / "raw/pcap/primary.pcap")["apdus"]:
        station = addresses.get(row["src_ip"], addresses.get(row["dst_ip"]))
        if station is None:
            raise ValueError("captured socket has no configured station")
        direction = "master_receive" if row["src_ip"] in addresses else "master_send"
        queue = queues[(station, direction, row["data_hex"])]
        if queue:
            queue.popleft()
    extras = [r for queue in queues.values() for r in queue]
    start = next(r["ts_event_utc_ns"] for r in events if r["event_type"] == "fault_active")
    end = min(r["ts_event_utc_ns"] for r in events if r["event_type"] == "restore_requested")
    result = resolve_test_attempts(
        extras,
        calls,
        {s["station_id"]: s["address"] for s in stations if s["station_id"] in targets},
        start,
        end,
    )
    return {
        **result,
        "syscall_count": len(calls),
        "scope": "A01_TESTFR_only_not_application_or_received_frames",
    }
