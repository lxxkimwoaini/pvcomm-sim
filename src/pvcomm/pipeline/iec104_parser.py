"""TShark-backed IEC 104 packet, APDU and object extraction."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any

from pvcomm.scenario.v01_models import load_v01_config

FIELDS = (
    "frame.number",
    "frame.time_epoch",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "frame.len",
    "tcp.len",
    "iec60870_104.type",
    "iec60870_104.tx",
    "iec60870_104.rx",
    "iec60870_asdu.typeid",
    "iec60870_asdu.causetx",
    "iec60870_asdu.addr",
    "iec60870_asdu.oa",
    "iec60870_asdu.ioa",
)


def _as_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value)
        except ValueError:
            return None


def _as_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _values(value: str) -> list[str]:
    return [part for part in value.split(",") if part]


def parse_run(run_dir: Path) -> dict[str, Any]:
    scenario_path = run_dir / "scenario.yaml"
    pcap_path = run_dir / "raw" / "pcap" / "primary_loopback.pcap"
    if not scenario_path.exists() or not pcap_path.exists():
        raise FileNotFoundError("run must contain scenario.yaml and raw/pcap/primary_loopback.pcap")
    config = load_v01_config(scenario_path)
    decode_overrides = [
        item
        for station in config.stations
        for item in ("-d", f"tcp.port=={station.port},iec60870_104")
    ]
    command = [
        "tshark",
        "-r",
        str(pcap_path),
        *decode_overrides,
        "-o",
        "tcp.desegment_tcp_streams:TRUE",
        "-o",
        "tcp.reassemble_out_of_order:TRUE",
        "-Y",
        "iec60870_104",
        "-T",
        "fields",
        "-E",
        "header=n",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=a",
        "-E",
        "quote=d",
    ]
    for field in FIELDS:
        command.extend(("-e", field))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"tshark parse failed ({result.returncode}): {result.stderr.strip()}")

    parsed_dir = run_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    packets_path = parsed_dir / "packets.jsonl"
    apdu_path = parsed_dir / "iec104_apdu.jsonl"
    objects_path = parsed_dir / "iec104_objects.jsonl"
    packet_count = 0
    apdu_count = 0
    object_count = 0
    with (
        packets_path.open("w", encoding="utf-8") as packets,
        apdu_path.open("w", encoding="utf-8") as apdus,
        objects_path.open("w", encoding="utf-8") as objects,
    ):
        reader = csv.reader(result.stdout.splitlines(), delimiter="\t", quotechar='"')
        for raw_row in reader:
            row = raw_row + [""] * (len(FIELDS) - len(raw_row))
            packet = {
                "run_id": config.run_id,
                "packet_index": _as_int(row[0]),
                "ts_capture_utc_ns": int((_as_float(row[1]) or 0) * 1_000_000_000),
                "src_ip": row[2] or None,
                "dst_ip": row[3] or None,
                "src_port": _as_int(row[4]),
                "dst_port": _as_int(row[5]),
                "packet_len": _as_int(row[6]),
                "tcp_payload_len": _as_int(row[7]),
                "parse_status": "iec104_dissected",
            }
            packets.write(json.dumps(packet, sort_keys=True) + "\n")
            packet_count += 1
            apdu = {
                **packet,
                "apdu_id": f"{config.run_id}-apdu-{apdu_count:08d}",
                "frame_type": row[8] or None,
                "send_seq": _as_int(row[9]),
                "recv_seq": _as_int(row[10]),
                "type_id": _as_int(row[11]),
                "cot": _as_int(row[12]),
                "common_address": _as_int(row[13]),
                "originator_address": _as_int(row[14]),
            }
            apdu_id = apdu["apdu_id"]
            apdus.write(json.dumps(apdu, sort_keys=True) + "\n")
            apdu_count += 1
            for object_index, ioa in enumerate(_values(row[15])):
                obj = {
                    "run_id": config.run_id,
                    "apdu_id": apdu_id,
                    "object_index": object_index,
                    "ioa": _as_int(ioa),
                    "type_id": apdu["type_id"],
                    "cot": apdu["cot"],
                    "common_address": apdu["common_address"],
                    "parse_status": "iec104_object",
                }
                objects.write(json.dumps(obj, sort_keys=True) + "\n")
                object_count += 1
    return {
        "run_id": config.run_id,
        "packet_count": packet_count,
        "apdu_count": apdu_count,
        "object_count": object_count,
        "parser": "tshark",
        "parser_fields": list(FIELDS),
    }
