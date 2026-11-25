"""Synthetic PDML unit fixtures covering repeated APDUs and precise time.

These XML fragments are parser fixtures, not network packets or research evidence.
"""

import json
from pathlib import Path

import pytest

from pvcomm.pipeline.iec104_parser import epoch_ns, extract_pdml


def field(name: str, value: str) -> str:
    return f'<field name="{name}" show="{value}"/>'


def packet(number: int, timestamp: str, protocol: str = "") -> str:
    return (
        "<packet><proto name='frame'>"
        + field("frame.number", str(number))
        + field("frame.time_epoch", timestamp)
        + "</proto>"
        + protocol
        + "</packet>"
    )


def test_epoch_timestamp_preserves_original_nanoseconds() -> None:
    assert epoch_ns("1795582877.808084999") == 1795582877808084999
    with pytest.raises(ValueError):
        epoch_ns("1.0000000001")


def test_ack_two_apdus_two_objects_and_reassembly_refs(tmp_path: Path) -> None:
    monitor = "<proto name='iec60870_104'>" + field("iec60870_104.type", "0") + "</proto>"
    asdu = (
        "<proto name='iec60870_asdu'>"
        + field("iec60870_asdu.typeid", "13")
        + field("iec60870_asdu.numix", "2")
        + "<field>"
        + field("iec60870_asdu.ioa", "1001")
        + field("iec60870_asdu.float", "90.5")
        + "</field>"
        + "<field>"
        + field("iec60870_asdu.ioa", "1006")
        + field("iec60870_asdu.float", "60.0")
        + "</field></proto>"
    )
    u_frame = "<proto name='iec60870_104'>" + field("iec60870_104.type", "3") + "</proto>"
    segment = "<proto name='tcp'>" + field("tcp.segment", "1") + "</proto>"
    path = tmp_path / "fixture.xml"
    path.write_text(
        "<pdml>"
        + packet(1, "1.000000001")
        + packet(2, "2.000000007", segment + monitor + asdu + u_frame)
        + "</pdml>",
        encoding="utf-8",
    )
    output = tmp_path / "tables"
    summary = extract_pdml(path, output, "synthetic_fixture")
    assert summary["packet_count"] == 2
    assert summary["apdu_count"] == 2
    assert summary["object_count"] == 2
    apdus = [json.loads(row) for row in (output / "iec104_apdu.jsonl").read_text().splitlines()]
    assert apdus[0]["source_frame_refs"] == [1, 2]
    assert apdus[0]["available_at"] == 2_000_000_007
    assert apdus[1]["type_id"] is None
    objects = [
        json.loads(row) for row in (output / "iec104_objects.jsonl").read_text().splitlines()
    ]
    assert [row["value_num"] for row in objects] == [90.5, 60.0]
