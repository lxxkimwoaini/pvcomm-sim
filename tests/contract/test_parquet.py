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


@pytest.mark.data
def test_value_changes_fail_even_if_schema_row_count_and_rehashed_manifest_match(
    tmp_path: Path,
) -> None:
    from pvcomm.pipeline.iec104_parser import PARSER_VERSION

    parsed = tmp_path / "parsed" / PARSER_VERSION
    parsed.mkdir(parents=True)
    (tmp_path / "observed").mkdir()
    (tmp_path / "truth").mkdir()
    (tmp_path / "manifest.json").write_text('{"status":"completed"}')
    for name in ("packets", "iec104_apdu", "iec104_objects"):
        (parsed / f"{name}.jsonl").write_text("")
    event = dict(
        run_id="synthetic-unit-only",
        station_id="PV01",
        event_id="unit-1",
        event_type="master_command_sent",
        ts_event_utc_ns=1,
        ts_mono_ns=1,
        sim_elapsed_ms=1,
        target_kw=10.0,
    )
    (tmp_path / "observed/master_events.jsonl").write_text(json.dumps(event) + "\n")
    export_parquet(tmp_path)
    assert verify_parquet(tmp_path)["passed"]
    pq = importlib.import_module("pyarrow.parquet")
    pa = importlib.import_module("pyarrow")
    table_path = tmp_path / "parsed/parquet-v01/master_events.parquet"
    table = pq.read_table(table_path)
    records = table.to_pylist()
    records[0]["payload_json"] = records[0]["payload_json"].replace("10.0", "11.0")
    pq.write_table(pa.Table.from_pylist(records, schema=table.schema), table_path)
    manifest_path = table_path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["master_events"]["sha256"] = hashlib.sha256(
        table_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    assert not verify_parquet(tmp_path)["passed"]


@pytest.mark.data
def test_large_evidence_is_streamed_and_last_batch_tampering_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pvcomm.pipeline.iec104_parser import PARSER_VERSION

    parsed = tmp_path / "parsed" / PARSER_VERSION
    parsed.mkdir(parents=True)
    (tmp_path / "observed").mkdir()
    (tmp_path / "truth").mkdir()
    (tmp_path / "manifest.json").write_text('{"status":"completed"}')
    for name in ("iec104_apdu", "iec104_objects"):
        (parsed / f"{name}.jsonl").write_text("")
    with (parsed / "packets.jsonl").open("w") as stream:
        for index in range(10_003):
            stream.write(
                json.dumps(
                    dict(
                        run_id="synthetic-unit-only",
                        capture_id="primary",
                        packet_index=index,
                        ts_capture_utc_ns=1795590000000000009 + index,
                    )
                )
                + "\n"
            )
    original_text, original_bytes = Path.read_text, Path.read_bytes

    def text_read(path: Path, *args: object, **kwargs: object) -> str:
        assert path.suffix != ".jsonl", "whole JSONL read is forbidden"
        return original_text(path, *args, **kwargs)  # type: ignore[arg-type]

    def bytes_read(path: Path) -> bytes:
        assert path.suffix not in (".jsonl", ".parquet"), "whole evidence read is forbidden"
        return original_bytes(path)

    pq = importlib.import_module("pyarrow.parquet")
    with monkeypatch.context() as bounded:
        bounded.setattr(Path, "read_text", text_read)
        bounded.setattr(Path, "read_bytes", bytes_read)
        bounded.setattr(pq, "read_table", lambda *_args, **_kwargs: pytest.fail("full Arrow read"))
        summary = export_parquet(tmp_path)
        assert summary["tables"]["packets"]["rows"] == 10_003
        assert verify_parquet(tmp_path)["passed"]
    path = tmp_path / "parsed/parquet-v01/packets.parquet"
    table = pq.read_table(path)
    assert table["ts_capture_utc_ns"][-1].as_py() == 1795590000000010011
    # An adversary changing both the last row and manifest hash must still fail.
    pa = importlib.import_module("pyarrow")
    rows = table.to_pylist()
    rows[-1]["packet_index"] += 1
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path, row_group_size=3000)
    metadata_path = path.parent / "manifest.json"
    metadata = json.loads(metadata_path.read_bytes())
    metadata["tables"]["packets"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    assert not verify_parquet(tmp_path)["passed"]
