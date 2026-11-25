"""Read-only M2 projections. The client cannot select paths or truth fields."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, PlainSerializer

from pvcomm.config.lab import load_v01_config
from pvcomm.config.models import Contract

Nanoseconds = Annotated[int, PlainSerializer(str, return_type=str, when_used="json")]


class RunSummary(Contract):
    run_key: str
    status: str
    source: Literal["real_c104_protocol_simulation"] = "real_c104_protocol_simulation"


class Point(Contract):
    available_at: Nanoseconds
    sim_elapsed_ms: int
    value: float


class StationView(Contract):
    station_id: str
    region_id: str
    common_address: int
    capacity_kw: float
    telemetry_period_s: int
    connection_state: str | None
    latest_power_kw: float | None
    power_available_at: Nanoseconds | None
    power: list[Point]


class EventView(Contract):
    event_id: str
    event_type: str
    available_at: Nanoseconds
    station_id: str | None
    ioa: int | None = None
    value: float | bool | None = None
    state: str | None = None
    action: str | None = None
    target_kw: float | None = None
    positive_confirmation: bool | None = None


class ProtocolView(Contract):
    apdu_id: str
    available_at: Nanoseconds
    frame_type: str
    type_id: int | None
    cot: int | None
    ca: int | None
    send_seq: int | None
    recv_seq: int | None


class RunSnapshot(Contract):
    run_key: str
    mode: Literal["M2"] = "M2"
    source: Literal["real_c104_protocol_simulation"] = "real_c104_protocol_simulation"
    cursor_utc_ns: Nanoseconds
    first_available_at: Nanoseconds
    last_available_at: Nanoseconds
    stations: list[StationView]
    events: list[EventView]
    protocol: list[ProtocolView]
    protocol_status: Literal["parsed", "not_yet_parsed"]
    limitations: str = "V0.1 协议测试模型；固定可用功率，未接入气象或潮流；功率为主站收到的遥测。"


class RunList(Contract):
    runs: list[RunSummary] = Field(default_factory=list)


class RunRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _directories(self) -> dict[str, Path]:
        if not self.root.is_dir():
            return {}
        return {
            hashlib.sha256(path.name.encode()).hexdigest()[:16]: path
            for path in self.root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and path.resolve().parent == self.root
            and (path / "manifest.json").is_file()
        }

    def _safe_file(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("evidence path escapes the configured root")
        return path

    def list_runs(self) -> RunList:
        runs = []
        for key, path in sorted(self._directories().items()):
            manifest = json.loads(
                self._safe_file(path / "manifest.json").read_text(encoding="utf-8")
            )
            if manifest.get("capture_topology") != "isolated_master_P0_and_RTU_P1":
                continue
            runs.append(RunSummary(run_key=key, status=manifest["status"]))
        return RunList(runs=runs)

    def snapshot(self, key: str, at_ns: int | None) -> RunSnapshot:
        directories = self._directories()
        if key not in directories:
            raise FileNotFoundError("unknown run")
        root = directories[key]
        config = load_v01_config(self._safe_file(root / "scenario.yaml"))
        rows = []
        for path in sorted((root / "observed").glob("master_events*.jsonl")):
            text = self._safe_file(path).read_text(encoding="utf-8")
            lines = text.splitlines()
            for index, line in enumerate(lines):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # A live writer may not yet have completed its final line.
                    if index == len(lines) - 1 and not text.endswith("\n"):
                        continue
                    raise
                if "available_at" not in row or not row.get("event_type", "").startswith("master_"):
                    raise ValueError("invalid master observation contract")
                rows.append(row)
        rows.sort(key=lambda r: (r["available_at"], r["event_id"]))
        first = rows[0]["available_at"] if rows else 0
        last = rows[-1]["available_at"] if rows else 0
        cursor = min(at_ns, last) if at_ns is not None else last
        visible = [row for row in rows if row["available_at"] <= cursor]
        stations = []
        for station in config.stations:
            own = [r for r in visible if r.get("station_id") == station.station_id]
            power = [
                r
                for r in own
                if r["event_type"] == "master_observed_point" and r.get("ioa") == 1001
            ]
            state = [r["state"] for r in own if r["event_type"] == "master_connection_state"]
            stations.append(
                StationView(
                    station_id=station.station_id,
                    region_id=station.region_id,
                    common_address=station.common_address,
                    capacity_kw=station.capacity_kw,
                    telemetry_period_s=station.telemetry_period_s,
                    connection_state=state[-1] if state else None,
                    latest_power_kw=power[-1]["value"] if power else None,
                    power_available_at=power[-1]["available_at"] if power else None,
                    power=[
                        Point(
                            available_at=r["available_at"],
                            sim_elapsed_ms=r["sim_elapsed_ms"],
                            value=r["value"],
                        )
                        for r in power[-600:]
                    ],
                )
            )
        event_fields = set(EventView.model_fields)
        events = [
            EventView.model_validate({k: r.get(k) for k in event_fields}) for r in visible[-100:]
        ]
        protocol_path = root / "parsed/tshark-pdml-v2/iec104_apdu.jsonl"
        protocol = []
        if protocol_path.exists():
            for line in self._safe_file(protocol_path).read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["available_at"] <= cursor:
                    protocol.append(
                        ProtocolView.model_validate({k: row[k] for k in ProtocolView.model_fields})
                    )
        return RunSnapshot(
            run_key=key,
            cursor_utc_ns=cursor,
            first_available_at=first,
            last_available_at=last,
            stations=stations,
            events=events,
            protocol=protocol[-100:],
            protocol_status="parsed" if protocol_path.exists() else "not_yet_parsed",
        )
