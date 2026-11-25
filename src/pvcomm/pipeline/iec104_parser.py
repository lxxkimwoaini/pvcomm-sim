"""Streaming PDML extraction preserving packet/APDU/object cardinality.

Versioned JSONL is development evidence, not the frozen Parquet dataset schema.
TShark performs TCP reassembly. XML protocol trees retain multiple APDUs per packet.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from pvcomm.config.lab import load_v01_config

PARSER_VERSION = "tshark-pdml-v2"


def epoch_ns(value: str) -> int:
    timestamp = Decimal(value) * 1_000_000_000
    if not timestamp.is_finite() or timestamp != timestamp.to_integral_value():
        raise ValueError(f"capture timestamp cannot be represented as nanoseconds: {value}")
    return int(timestamp)


def _field(node: ET.Element, name: str) -> str | None:
    child = node.find(f".//field[@name='{name}']")
    return child.get("show") if child is not None else None


def _integer(node: ET.Element, name: str) -> int | None:
    text = _field(node, name)
    return int(text, 16 if text.startswith("0x") else 10) if text else None


def _bool(node: ET.Element, name: str) -> bool | None:
    text = _field(node, name)
    return text == "True" if text is not None else None


def _write(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, allow_nan=False) + "\n")


def extract_pdml(source: Path, output: Path, run_id: str) -> dict[str, int]:
    """Extract unfiltered packets and scoped protocol trees into a new directory."""
    output.mkdir(exist_ok=False)
    counts = {"packet_count": 0, "apdu_count": 0, "object_count": 0, "incomplete_apdu_count": 0}
    capture_times: dict[int, int] = {}
    with (
        (output / "packets.jsonl").open("x", encoding="utf-8") as packets,
        (output / "iec104_apdu.jsonl").open("x", encoding="utf-8") as apdus,
        (output / "iec104_objects.jsonl").open("x", encoding="utf-8") as objects,
    ):
        context = ET.iterparse(source, events=("start", "end"))
        _, root = next(context)
        for event, packet in context:
            if event != "end" or packet.tag != "packet":
                continue
            index = _integer(packet, "frame.number")
            timestamp = _field(packet, "frame.time_epoch")
            if index is None or timestamp is None:
                raise ValueError("PDML packet is missing capture identity or time")
            capture_times[index] = epoch_ns(timestamp)
            refs = sorted(
                {
                    index,
                    *(
                        int(field.get("show", "0"))
                        for field in packet.findall(".//field[@name='tcp.segment']")
                    ),
                }
            )
            if any(ref not in capture_times for ref in refs):
                raise ValueError("reassembly references an unavailable source frame")
            record = {
                "run_id": run_id,
                "capture_id": "primary",
                "packet_index": index,
                "ts_capture_utc_ns": capture_times[index],
                "src_ip": _field(packet, "ip.src"),
                "dst_ip": _field(packet, "ip.dst"),
                "src_port": _integer(packet, "tcp.srcport"),
                "dst_port": _integer(packet, "tcp.dstport"),
                "packet_len": _integer(packet, "frame.len"),
                "tcp_payload_len": _integer(packet, "tcp.len"),
                "tcp_flags": _field(packet, "tcp.flags"),
                "flow_id": _integer(packet, "tcp.stream"),
                "tcp_retransmission": packet.find(".//field[@name='tcp.analysis.retransmission']")
                is not None,
            }
            _write(packets, record)
            counts["packet_count"] += 1
            protocols = list(packet.findall("proto"))
            for position, protocol in enumerate(protocols):
                if protocol.get("name") != "iec60870_104":
                    continue
                frame_type = {0: "I", 1: "S", 3: "U"}.get(
                    _integer(protocol, "iec60870_104.type") or 0
                )
                asdu = protocols[position + 1] if position + 1 < len(protocols) else None
                if asdu is not None and asdu.get("name") != "iec60870_asdu":
                    asdu = None
                status = "complete" if frame_type != "I" or asdu is not None else "incomplete"
                apdu = {
                    **record,
                    "apdu_id": f"primary-{index}-{position}",
                    "frame_type": frame_type,
                    "send_seq": _integer(protocol, "iec60870_104.tx"),
                    "recv_seq": _integer(protocol, "iec60870_104.rx"),
                    "u_function": _integer(protocol, "iec60870_104.utype"),
                    "source_frame_refs": refs,
                    "available_at": max(capture_times[ref] for ref in refs),
                    "parse_status": status,
                    "type_id": _integer(asdu, "iec60870_asdu.typeid") if asdu is not None else None,
                    "cot": _integer(asdu, "iec60870_asdu.causetx") if asdu is not None else None,
                    "ca": _integer(asdu, "iec60870_asdu.addr") if asdu is not None else None,
                    "oa": _integer(asdu, "iec60870_asdu.oa") if asdu is not None else None,
                    "negative_confirm": _bool(asdu, "iec60870_asdu.nega")
                    if asdu is not None
                    else None,
                    "test_flag": _bool(asdu, "iec60870_asdu.test") if asdu is not None else None,
                    "sq": _bool(asdu, "iec60870_asdu.sq") if asdu is not None else None,
                }
                _write(apdus, apdu)
                counts["apdu_count"] += 1
                counts["incomplete_apdu_count"] += int(status == "incomplete")
                if asdu is None:
                    continue
                groups = [
                    child
                    for child in asdu
                    if child.find(".//field[@name='iec60870_asdu.ioa']") is not None
                ]
                for object_index, group in enumerate(groups):
                    value_text = _field(group, "iec60870_asdu.float")
                    value = float(value_text) if value_text is not None else None
                    value_status = "present" if value is not None else "absent"
                    if value is not None and not math.isfinite(value):
                        value = None
                        value_status = "non_finite"
                    _write(
                        objects,
                        {
                            **apdu,
                            "object_index": object_index,
                            "ioa": _integer(group, "iec60870_asdu.ioa"),
                            "value_num": value,
                            "value_status": value_status,
                            "value_enum": _field(group, "iec60870_asdu.siq.spi"),
                            "quality_raw": _integer(group, "iec60870_asdu.qds"),
                            "ts_device": _field(group, "iec60870_asdu.cp56time"),
                        },
                    )
                    counts["object_count"] += 1
                expected = _integer(asdu, "iec60870_asdu.numix")
                if expected is not None and expected != len(groups):
                    raise ValueError("TShark object structure does not match ASDU object count")
            packet.clear()
            root.clear()
    return counts


def parse_run(run_dir: Path) -> dict[str, Any]:
    config = load_v01_config(run_dir / "scenario.yaml")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "completed":
        raise ValueError("parsing requires a finalized run; partial evidence remains preserved")
    pcap = run_dir / (
        "raw/pcap/primary.pcap"
        if manifest.get("capture_topology") == "isolated_master_P0_and_RTU_P1"
        else "raw/pcap/primary_loopback.pcap"
    )
    destination = run_dir / "parsed" / PARSER_VERSION
    if destination.exists():
        raise FileExistsError(f"parser output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["tshark", "-n", "-r", str(pcap)]
    for station in config.stations:
        command.extend(("-d", f"tcp.port=={station.port},iec60870_104"))
    command.extend(
        (
            "-o",
            "tcp.desegment_tcp_streams:TRUE",
            "-o",
            "tcp.reassemble_out_of_order:TRUE",
            "-T",
            "pdml",
        )
    )
    with tempfile.TemporaryDirectory(prefix=".parse-", dir=destination.parent) as temp:
        work = Path(temp)
        with (work / "capture.pdml").open("wb") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError(f"TShark failed: {result.stderr.decode(errors='replace')[-2000:]}")
        summary = {
            "run_id": config.run_id,
            "parser_version": PARSER_VERSION,
            "pcap_sha256": hashlib.sha256(pcap.read_bytes()).hexdigest(),
            **extract_pdml(work / "capture.pdml", work / "tables", config.run_id),
        }
        (work / "tables/summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        fields = subprocess.run(["tshark", "-G", "fields"], capture_output=True, check=True)
        (work / "tables/iec104_fields.tsv").write_bytes(
            b"\n".join(line for line in fields.stdout.splitlines() if b"iec60870" in line) + b"\n"
        )
        (work / "tables").rename(destination)
    return summary
