"""Explicit, versioned Arrow contracts for V0.1 protocol evidence.

This is a development evidence version, not a released V1 research dataset.
Raw JSONL and PCAP remain immutable inputs; writes commit as one directory.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.pipeline.iec104_parser import PARSER_VERSION

VERSION = "parquet-v01"
REQUIRED = {
    "run_id",
    "capture_id",
    "packet_index",
    "ts_capture_utc_ns",
    "apdu_id",
    "frame_type",
    "parse_status",
    "source_frame_refs",
    "event_id",
    "event_type",
    "ts_event_utc_ns",
    "ts_mono_ns",
    "sim_elapsed_ms",
    "object_index",
    "ioa",
}
PACKET_FIELDS = {
    "run_id": "string",
    "capture_id": "string",
    "packet_index": "int64",
    "ts_capture_utc_ns": "int64",
    "src_ip": "string",
    "dst_ip": "string",
    "src_port": "int64",
    "dst_port": "int64",
    "packet_len": "int64",
    "tcp_payload_len": "int64",
    "tcp_flags": "string",
    "flow_id": "int64",
    "tcp_retransmission": "bool",
}
APDU_FIELDS = {
    **PACKET_FIELDS,
    "apdu_id": "string",
    "frame_type": "string",
    "send_seq": "int64",
    "recv_seq": "int64",
    "u_function": "int64",
    "source_frame_refs": "list_int64",
    "available_at": "int64",
    "parse_status": "string",
    "type_id": "int64",
    "cot": "int64",
    "ca": "int64",
    "oa": "int64",
    "negative_confirm": "bool",
    "test_flag": "bool",
    "sq": "bool",
}
OBJECT_FIELDS = {
    **APDU_FIELDS,
    "object_index": "int64",
    "ioa": "int64",
    "value_num": "float64",
    "value_status": "string",
    "value_enum": "string",
    "quality_raw": "int64",
    "ts_device": "string",
}
EVENT_FIELDS = {
    "run_id": "string",
    "event_id": "string",
    "event_type": "string",
    "ts_event_utc_ns": "int64",
    "ts_mono_ns": "int64",
    "sim_elapsed_ms": "int64",
    "available_at": "int64",
    "station_id": "string",
    "payload_json": "string",
}


def read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for path in sorted(paths)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def arrow_schema(fields: dict[str, str], scope: str) -> Any:
    pa = importlib.import_module("pyarrow")
    types = {
        "string": pa.string(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "bool": pa.bool_(),
        "list_int64": pa.list_(pa.field("element", pa.int64())),
    }
    return pa.schema(
        [
            pa.field(name, types[kind], nullable=name not in REQUIRED)
            for name, kind in fields.items()
        ],
        metadata={
            b"pvcomm_schema": b"0.1-protocol-evidence",
            b"scope": scope.encode(),
            b"utc_unit": b"ns",
            b"timezone": b"UTC",
            b"point_units": b"1001:kW,1002:kvar,1003:kV,1006:kW,3001:kW",
        },
    )


def export_parquet(run_dir: Path) -> dict[str, Any]:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    parsed = run_dir / "parsed" / PARSER_VERSION
    destination = run_dir / "parsed" / VERSION
    if destination.exists():
        raise FileExistsError(f"Parquet export exists: {destination}")
    if json.loads((run_dir / "manifest.json").read_text())["status"] != "completed":
        raise ValueError("export requires completed evidence")
    tables: dict[str, tuple[list[dict[str, Any]], dict[str, str], str]] = {
        name: (read_jsonl([parsed / f"{name}.jsonl"]), fields, "M1_primary")
        for name, fields in (
            ("packets", PACKET_FIELDS),
            ("iec104_apdu", APDU_FIELDS),
            ("iec104_objects", OBJECT_FIELDS),
        )
    }
    sources = list(parsed.glob("*.jsonl"))
    for name, paths, scope in (
        ("master_events", list((run_dir / "observed").glob("master_events*.jsonl")), "M2"),
        ("terminal_events", list((run_dir / "truth").glob("terminal_events*.jsonl")), "Oracle"),
        ("electrical_state", list((run_dir / "truth").glob("electrical_state*.jsonl")), "Oracle"),
    ):
        sources.extend(paths)
        records = read_jsonl(paths)
        rows = [
            {
                **{key: row.get(key) for key in EVENT_FIELDS if key != "payload_json"},
                "payload_json": json.dumps(
                    {k: v for k, v in row.items() if k not in EVENT_FIELDS},
                    allow_nan=False,
                    sort_keys=True,
                ),
            }
            for row in records
        ]
        tables[name] = (rows, EVENT_FIELDS, scope)
    summary: dict[str, Any] = {
        "schema_version": "0.1-protocol-evidence",
        "tables": {},
        "source_sha256": {
            p.relative_to(run_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        },
    }
    with tempfile.TemporaryDirectory(prefix=".parquet-", dir=destination.parent) as temp:
        stage = Path(temp) / "tables"
        stage.mkdir()
        for name, (rows, fields, scope) in tables.items():
            schema = arrow_schema(fields, scope)
            table = pa.Table.from_pylist(rows, schema=schema)
            for column in schema:
                if not column.nullable and table[column.name].null_count:
                    raise ValueError(f"required Parquet field missing: {name}.{column.name}")
            path = stage / f"{name}.parquet"
            pq.write_table(table, path, version="2.6", compression="zstd")
            restored = pq.read_table(path)
            if restored.num_rows != len(rows) or not restored.schema.equals(
                schema, check_metadata=True
            ):
                raise ValueError(f"Parquet round trip failed: {name}")
            summary["tables"][name] = {
                "rows": len(rows),
                "scope": scope,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        (stage / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return summary


def verify_parquet(run_dir: Path) -> dict[str, Any]:
    """Recheck all six typed tables and immutable source hashes, not just existence."""
    pq = importlib.import_module("pyarrow.parquet")
    destination = run_dir / "parsed" / VERSION
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        return {"passed": False, "reason": "missing Parquet manifest"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = {
        "packets": (PACKET_FIELDS, "M1_primary"),
        "iec104_apdu": (APDU_FIELDS, "M1_primary"),
        "iec104_objects": (OBJECT_FIELDS, "M1_primary"),
        "master_events": (EVENT_FIELDS, "M2"),
        "terminal_events": (EVENT_FIELDS, "Oracle"),
        "electrical_state": (EVENT_FIELDS, "Oracle"),
    }
    paths = list((run_dir / "parsed" / PARSER_VERSION).glob("*.jsonl"))
    paths += list((run_dir / "observed").glob("master_events*.jsonl"))
    paths += list((run_dir / "truth").glob("terminal_events*.jsonl"))
    paths += list((run_dir / "truth").glob("electrical_state*.jsonl"))
    sources = {
        p.relative_to(run_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    source_match = manifest.get("source_sha256") == sources
    tables = {}
    for name, (fields, scope) in contracts.items():
        path = destination / f"{name}.parquet"
        entry = manifest.get("tables", {}).get(name, {})
        if not path.is_file():
            tables[name] = {"passed": False, "reason": "missing table"}
            continue
        table = pq.read_table(path)
        schema = arrow_schema(fields, scope)
        if name in ("packets", "iec104_apdu", "iec104_objects"):
            table_sources = [run_dir / "parsed" / PARSER_VERSION / f"{name}.jsonl"]
        else:
            folder = "observed" if name == "master_events" else "truth"
            table_sources = list((run_dir / folder).glob(f"{name}*.jsonl"))
        source_rows = len(read_jsonl(table_sources))
        passed = (
            entry.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
            and entry.get("rows") == table.num_rows
            and table.num_rows == source_rows
            and entry.get("scope") == scope
            and table.schema.equals(schema, check_metadata=True)
            and all(table[c.name].null_count == 0 for c in schema if not c.nullable)
        )
        tables[name] = {"passed": passed, "rows": table.num_rows}
    return {
        "passed": source_match
        and manifest.get("schema_version") == "0.1-protocol-evidence"
        and set(manifest.get("tables", {})) == set(contracts)
        and all(t["passed"] for t in tables.values()),
        "source_hashes_match": source_match,
        "tables": tables,
    }
