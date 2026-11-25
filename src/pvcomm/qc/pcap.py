"""Independent classic-PCAP/TCP reassembly for the bounded IPv4 lab.

This reads real captured bytes. It does not create or transmit protocol frames.
Unsupported/truncated captures fail explicitly instead of inflating parse rates.
"""

from __future__ import annotations

import socket
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Stream:
    base_seq: int
    cursor: int = 0
    pending: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    history: dict[int, int] = field(default_factory=dict)
    assembled: list[tuple[int, int, int]] = field(default_factory=list)

    def feed(self, seq: int, data: bytes, index: int, timestamp: int) -> list[dict[str, Any]]:
        offset = (seq - self.base_seq) % (2**32)
        if offset > 2**31:
            raise ValueError("TCP sequence predates the captured handshake")
        for i, value in enumerate(data, start=offset):
            if i in self.history and self.history[i] != value:
                raise ValueError("conflicting TCP retransmission")
            if i >= self.cursor:
                self.pending.setdefault(i, (value, index, timestamp))
            self.history[i] = value
        while self.cursor in self.pending:
            self.assembled.append(self.pending.pop(self.cursor))
            self.cursor += 1
        results = []
        while len(self.assembled) >= 2:
            if self.assembled[0][0] != 0x68:
                raise ValueError("TCP application stream does not start with IEC 104")
            size = self.assembled[1][0] + 2
            if not 6 <= size <= 255:
                raise ValueError("invalid IEC 104 APDU length")
            if len(self.assembled) < size:
                break
            pieces, self.assembled = self.assembled[:size], self.assembled[size:]
            raw = bytes(item[0] for item in pieces)
            frame_type = "I" if raw[2] & 1 == 0 else "S" if raw[2] & 3 == 1 else "U"
            results.append(
                {
                    "data_hex": raw.hex(),
                    "frame_type": frame_type,
                    "send_seq": int.from_bytes(raw[2:4], "little") >> 1
                    if frame_type == "I"
                    else None,
                    "recv_seq": int.from_bytes(raw[4:6], "little") >> 1
                    if frame_type in ("I", "S")
                    else None,
                    "type_id": raw[6] if frame_type == "I" and len(raw) >= 12 else None,
                    "cot": raw[8] & 0x3F if frame_type == "I" and len(raw) >= 12 else None,
                    "ca": int.from_bytes(raw[10:12], "little")
                    if frame_type == "I" and len(raw) >= 12
                    else None,
                    "source_frame_refs": sorted({item[1] for item in pieces}),
                    "available_at": max(item[2] for item in pieces),
                }
            )
        return results


def read_apdus(path: Path) -> dict[str, Any]:
    streams: dict[tuple[str, str, int, int], Stream] = {}
    generations: dict[tuple[str, str, int, int], int] = defaultdict(int)
    records: list[dict[str, Any]] = []
    completed_streams: list[Stream] = []
    packet_count = 0
    with path.open("rb") as source:
        header = source.read(24)
        formats = {
            b"\xd4\xc3\xb2\xa1": ("<", 1000),
            b"\xa1\xb2\xc3\xd4": (">", 1000),
            b"\x4d\x3c\xb2\xa1": ("<", 1),
            b"\xa1\xb2\x3c\x4d": (">", 1),
        }
        if header[:4] not in formats or len(header) != 24:
            raise ValueError("only complete classic PCAP is supported")
        endian, multiplier = formats[header[:4]]
        linktype = struct.unpack(endian + "I", header[20:24])[0]
        if linktype not in (1, 113, 276):
            raise ValueError(f"unsupported PCAP link type: {linktype}")
        while record_header := source.read(16):
            if len(record_header) != 16:
                raise ValueError("truncated PCAP record header")
            sec, subsec, size, original = struct.unpack(endian + "IIII", record_header)
            packet = source.read(size)
            if len(packet) != size or size != original:
                raise ValueError("truncated captured packet")
            packet_count += 1
            timestamp = sec * 1_000_000_000 + subsec * multiplier
            network_offset = {1: 14, 113: 16, 276: 20}[linktype]
            ethertype_offset = {1: 12, 113: 14, 276: 0}[linktype]
            ethertype = int.from_bytes(packet[ethertype_offset : ethertype_offset + 2], "big")
            if ethertype != 0x800:
                continue
            ip = packet[network_offset:]
            if len(ip) < 20 or ip[0] >> 4 != 4:
                raise ValueError("invalid IPv4 packet")
            ihl = (ip[0] & 15) * 4
            total = int.from_bytes(ip[2:4], "big")
            if len(ip) < total or int.from_bytes(ip[6:8], "big") & 0x3FFF:
                raise ValueError("fragmented or truncated IPv4 is unsupported")
            if ip[9] != 6:
                continue
            tcp = ip[ihl:total]
            if len(tcp) < 20:
                raise ValueError("truncated TCP header")
            sport, dport, seq = struct.unpack("!HHI", tcp[:8])
            key = (socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20]), sport, dport)
            syn = bool(tcp[13] & 2)
            if syn and (key not in streams or streams[key].base_seq != (seq + 1) % 2**32):
                if key in streams:
                    completed_streams.append(streams[key])
                streams[key] = Stream((seq + 1) % 2**32)
                generations[key] += 1
            payload = tcp[(tcp[12] >> 4) * 4 :]
            if not payload:
                continue
            if key not in streams:
                raise ValueError("application data without a captured TCP handshake")
            for row in streams[key].feed(
                (seq + int(syn)) % 2**32, payload, packet_count, timestamp
            ):
                records.append(
                    {
                        **row,
                        "packet_index": row["source_frame_refs"][-1],
                        "src_ip": key[0],
                        "dst_ip": key[1],
                        "src_port": sport,
                        "dst_port": dport,
                        "connection_generation": generations[key],
                    }
                )
    all_streams = [*completed_streams, *streams.values()]
    incomplete = sum(len(s.assembled) + len(s.pending) for s in all_streams)
    return {
        "packet_count": packet_count,
        "complete_apdu_count": len(records),
        "incomplete_byte_count": incomplete,
        "apdus": records,
    }
