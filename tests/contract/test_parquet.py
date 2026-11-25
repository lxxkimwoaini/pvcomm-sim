"""Synthetic Arrow contract fixtures, not formal simulation output."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from pvcomm.storage.parquet import PACKET_FIELDS, arrow_schema, export_parquet, verify_parquet


@pytest.mark.data
def test_arrow_preserves_nanoseconds_units_and_schema(tmp_path: Path) -> None:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    schema = arrow_schema(PACKET_FIELDS, "M1_primary")
    row = {
        "run_id": "synthetic",
        "capture_id": "primary",
        "packet_index": 1,
        "ts_capture_utc_ns": 1795590000000000009,
    }
    table = pa.Table.from_pylist([row], schema=schema)
    path = tmp_path / "fixture.parquet"
    pq.write_table(table, path, version="2.6")
    restored = pq.read_table(path)
    assert restored.schema == schema
    assert restored.to_pylist()[0]["ts_capture_utc_ns"] == 1795590000000000009
    assert schema.metadata[b"utc_unit"] == b"ns"
    assert not schema.field("packet_index").nullable


@pytest.mark.data
def test_arrow_rejects_incompatible_field_types() -> None:
    pa = importlib.import_module("pyarrow")
    with pytest.raises((ValueError, TypeError)):
        pa.Table.from_pylist(
            [{"packet_index": "not-an-integer"}], schema=arrow_schema(PACKET_FIELDS, "M1_primary")
        )


@pytest.mark.data
def test_export_rejects_overwrite_and_qc_detects_tampering(tmp_path: Path) -> None:
    from pvcomm.pipeline.iec104_parser import PARSER_VERSION

    parsed = tmp_path / "parsed" / PARSER_VERSION
    parsed.mkdir(parents=True)
    (tmp_path / "observed").mkdir()
    (tmp_path / "truth").mkdir()
    (tmp_path / "manifest.json").write_text('{"status":"completed"}')
    for name in ("packets", "iec104_apdu", "iec104_objects"):
        (parsed / f"{name}.jsonl").write_text("")
    export_parquet(tmp_path)
    assert verify_parquet(tmp_path)["passed"]
    with pytest.raises(FileExistsError):
        export_parquet(tmp_path)
    source = parsed / "packets.jsonl"
    source.write_text("\n")
    assert not verify_parquet(tmp_path)["source_hashes_match"]
    source.write_text("")
    destination = tmp_path / "parsed/parquet-v01"
    table_path = destination / "packets.parquet"
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    pq.write_table(pa.table({"wrong": [1]}), table_path)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["packets"]["sha256"] = hashlib.sha256(table_path.read_bytes()).hexdigest()
    manifest["tables"]["packets"]["rows"] = 1
    manifest_path.write_text(json.dumps(manifest))
    assert not verify_parquet(tmp_path)["passed"]
