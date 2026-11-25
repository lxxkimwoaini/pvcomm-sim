"""Additional Oracle-only Arrow tables; V0.1 six-table contract stays intact."""

import hashlib
import importlib
import json
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.storage.parquet import read_jsonl

VERSION = "parquet-v02-power"
ELECTRICAL_FIELDS = {
    "run_id": "string",
    "event_id": "string",
    "station_id": "string",
    "ts_event_utc_ns": "int64",
    "ts_mono_ns": "int64",
    "model_mono_ns": "int64",
    "sim_elapsed_ms": "int64",
    "source_weather_ts": "string",
    "source_interval_start": "string",
    "ghi_w_m2": "float64",
    "dni_w_m2": "float64",
    "dhi_w_m2": "float64",
    "temp_air_c": "float64",
    "wind_speed_m_s": "float64",
    "poa_w_m2": "float64",
    "cell_temp_c": "float64",
    "p_avail_kw": "float64",
    "p_actual_kw": "float64",
    "q_actual_kvar": "float64",
    "p_set_accepted_kw": "float64",
    "running": "bool",
    "connected": "bool",
    "faulted": "bool",
    "command_count": "int64",
    "constraint_reason": "string",
    "source": "string",
}
FLOW_FIELDS = {
    "run_id": "string",
    "event_id": "string",
    "station_id": "string",
    "ts_event_utc_ns": "int64",
    "ts_mono_ns": "int64",
    "sim_elapsed_ms": "int64",
    "solved_mono_ns": "int64",
    "injection_mono_ns": "int64",
    "converged": "bool",
    "voltage_pu": "float64",
    "voltage_kv": "float64",
    "current_a": "float64",
    "p_actual_kw": "float64",
    "q_actual_kvar": "float64",
    "source": "string",
}
TABLES = {"electrical_state": ELECTRICAL_FIELDS, "powerflow": FLOW_FIELDS}


def schema(fields: dict[str, str]) -> Any:
    pa = importlib.import_module("pyarrow")
    return pa.schema(
        [
            pa.field(name, getattr(pa, "bool_" if kind == "bool" else kind)(), nullable=False)
            for name, kind in fields.items()
        ],
        metadata={
            b"pvcomm_schema": b"0.2-power-evidence",
            b"scope": b"Oracle",
            b"units": b"P:kW,Q:kvar,S:kVA,voltage:line-line-kV,current:balanced-A,irradiance:W/m2",
            b"time": b"UTC-ns;monotonic-ns;source_weather_ts:ISO8601-UTC",
        },
    )


def export_power_parquet(root: Path) -> dict[str, Any]:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    destination = root / "parsed" / VERSION
    if destination.exists():
        raise FileExistsError(f"power Arrow output exists: {destination}")
    sources = [
        *sorted((root / "truth").glob("electrical_state*.jsonl")),
        *sorted((root / "truth").glob("terminal_events*.jsonl")),
        root / "power_config.json",
        root / "truth/weather/weather.json",
        root / "truth/weather/source.json",
    ]
    rows = {
        "electrical_state": [
            r
            for r in read_jsonl(sorted((root / "truth").glob("electrical_state*.jsonl")))
            if r.get("source") == "pvlib_pvwatts_inverter_v02"
        ],
        "powerflow": [
            r
            for r in read_jsonl(sorted((root / "truth").glob("terminal_events*.jsonl")))
            if r["event_type"] == "terminal_powerflow_solved"
        ],
    }
    manifest: dict[str, Any] = {
        "version": VERSION,
        "scope": "Oracle",
        "tables": {},
        "sources": {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        },
    }
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".power-arrow-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "tables"
        stage.mkdir()
        for name, fields in TABLES.items():
            table = pa.Table.from_pylist(rows[name], schema=schema(fields))
            if not table.num_rows or any(table[c].null_count for c in fields):
                raise ValueError(f"empty or incomplete {name} power table")
            path = stage / f"{name}.parquet"
            pq.write_table(table, path)
            if not pq.read_schema(path).equals(schema(fields), check_metadata=True):
                raise ValueError("power Arrow schema roundtrip mismatch")
            manifest["tables"][name] = {
                "rows": table.num_rows,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        stage.rename(destination)
    return manifest


def verify_power_parquet(root: Path) -> bool:
    pq = importlib.import_module("pyarrow.parquet")
    destination = root / "parsed" / VERSION
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("scope") != "Oracle" or set(manifest["tables"]) != set(TABLES):
        return False
    for relative, digest in manifest["sources"].items():
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("power source escapes run directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False
    for name, fields in TABLES.items():
        path = destination / f"{name}.parquet"
        table = pq.read_table(path)
        record = manifest["tables"][name]
        if (
            not table.schema.equals(schema(fields), check_metadata=True)
            or table.num_rows != record["rows"]
            or table.num_rows == 0
            or any(table[c].null_count for c in fields)
            or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
        ):
            return False
    return True
