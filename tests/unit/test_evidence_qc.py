"""Synthetic byte fixtures are solely for verifier unit regression tests."""

import struct
from pathlib import Path

import pytest

from pvcomm.qc.pcap import Stream, read_apdus
from pvcomm.qc.v01 import shutdown_capture_tail


def test_reassembly_orders_fragments_and_deduplicates_retransmission() -> None:
    stream = Stream(100)
    assert stream.feed(103, bytes.fromhex("000000"), 1, 20) == []
    messages = stream.feed(100, bytes.fromhex("680407"), 2, 30)
    assert len(messages) == 1
    assert messages[0]["source_frame_refs"] == [1, 2]
    assert messages[0]["available_at"] == 30
    assert messages[0]["data_hex"] == "680407000000"
    assert stream.feed(100, bytes.fromhex("680407000000"), 3, 40) == []
    with pytest.raises(ValueError, match="conflicting"):
        stream.feed(100, bytes.fromhex("68040b000000"), 4, 50)


def test_multiple_apdus_and_sequence_wrap() -> None:
    stream = Stream(2**32 - 3)
    assert stream.feed(2**32 - 3, bytes.fromhex("680407"), 1, 1) == []
    rows = stream.feed(0, bytes.fromhex("00000068040b000000"), 2, 2)
    assert len(rows) == 2
    assert all(row["frame_type"] == "U" for row in rows)


def test_complete_out_of_order_pdu_waits_for_tcp_sequence_gap() -> None:
    stream = Stream(100)
    later = bytes.fromhex("68040b000000")
    earlier = bytes.fromhex("680407000000")
    assert stream.feed(106, later, 1, 10) == []
    rows = stream.feed(100, earlier, 2, 20)
    assert [r["data_hex"] for r in rows] == [earlier.hex(), later.hex()]
    assert rows[1]["source_frame_refs"] == [1, 2]
    assert rows[1]["available_at"] == 20


def test_truncated_capture_is_not_counted_as_parse_success(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.pcap"
    path.write_bytes(
        b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1) + b"partial"
    )
    with pytest.raises(ValueError, match="truncated"):
        read_apdus(path)


def test_capture_tail_is_outside_the_declared_business_interval() -> None:
    events = [
        {"event_type": "master_business_finished", "ts_event_utc_ns": 1000},
        {"event_type": "master_stopped", "ts_event_utc_ns": 2000},
    ]
    assert shutdown_capture_tail({"frame_type": "S", "available_at": 1500}, events)
    assert not shutdown_capture_tail({"frame_type": "S", "available_at": 999}, events)
    assert shutdown_capture_tail({"frame_type": "I", "available_at": 1500}, events)
    assert not shutdown_capture_tail({"frame_type": "I", "available_at": 999}, events)
    assert not shutdown_capture_tail({"frame_type": "I", "available_at": 200_000_000}, events)
