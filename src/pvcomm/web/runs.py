"""Read-only M2 projections. The client cannot select paths or truth fields."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, PlainSerializer

from pvcomm.config.lab import load_v01_config
from pvcomm.config.models import Contract
from pvcomm.web.observation_index import ObservationIndex

Nanoseconds = Annotated[int, PlainSerializer(str, return_type=str, when_used="json")]


class RunSummary(Contract):
    run_key: str
    status: str
    source: Literal["real_c104_protocol_simulation"] = "real_c104_protocol_simulation"


class Point(Contract):
    available_at: Nanoseconds
    sim_elapsed_ms: int
    value: float


class MeasurementSeries(Contract):
    ioa: int
    name: str
    unit: str
    source: Literal["master_iec104_telemetry"] = "master_iec104_telemetry"
    points: list[Point]


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
    measurements: list[MeasurementSeries] = Field(default_factory=list)


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
        self._cache = tempfile.TemporaryDirectory(prefix="pvcomm-m2-index-")
        self._indexes: dict[str, ObservationIndex] = {}
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, self._close_indexes, self._indexes, self._cache)

    @staticmethod
    def _close_indexes(
        indexes: dict[str, ObservationIndex], cache: tempfile.TemporaryDirectory[str]
    ) -> None:
        for index in indexes.values():
            index.connection.close()
        indexes.clear()
        cache.cleanup()

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
        with self._lock:
            return self._snapshot(key, at_ns)

    def _snapshot(self, key: str, at_ns: int | None) -> RunSnapshot:
        directories = self._directories()
        if key not in directories:
            raise FileNotFoundError("unknown run")
        root = directories[key]
        config = load_v01_config(self._safe_file(root / "scenario.yaml"))
        if key not in self._indexes:
            self._indexes[key] = ObservationIndex(Path(self._cache.name) / f"{key}.sqlite")
        index = self._indexes[key]
        for path in sorted((root / "observed").glob("master_events*.jsonl")):
            index.ingest(self._safe_file(path))
        protocol_path = root / "parsed/tshark-pdml-v2/iec104_apdu.jsonl"
        if protocol_path.exists():
            index.ingest(self._safe_file(protocol_path), protocol=True)
        first, last = index.bounds()
        cursor = min(at_ns, last) if at_ns is not None else last
        stations = []
        for station in config.stations:
            own = []
            for ioa in (1001, 1002, 1003, 1004, 1005, 1006, 1008):
                own.extend(
                    index.rows(
                        cursor,
                        station=station.station_id,
                        kind="master_observed_point",
                        ioa=ioa,
                        limit=600,
                    )
                )
            power = [r for r in own if r.get("ioa") == 1001]
            state = [
                r["state"]
                for r in index.rows(
                    cursor, station=station.station_id, kind="master_connection_state", limit=1
                )
            ]
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
                    measurements=[
                        MeasurementSeries(
                            ioa=ioa,
                            name=name,
                            unit=unit,
                            points=[
                                Point(
                                    available_at=r["available_at"],
                                    sim_elapsed_ms=r["sim_elapsed_ms"],
                                    value=r["value"],
                                )
                                for r in own
                                if r["event_type"] == "master_observed_point"
                                and r.get("ioa") == ioa
                            ][-600:],
                        )
                        for ioa, name, unit in (
                            (1001, "actual_power", "kW"),
                            (1002, "reactive_power", "kvar"),
                            (1003, "pcc_line_voltage", "kV"),
                            (1004, "current", "A"),
                            (1005, "irradiance", "W/m2"),
                            (1006, "accepted_target", "kW"),
                            (1008, "available_ac_power", "kW"),
                        )
                    ],
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
            EventView.model_validate({k: r.get(k) for k in event_fields})
            for r in index.rows(cursor)
        ]
        protocol = [ProtocolView.model_validate(row) for row in index.rows(cursor, kind="protocol")]
        return RunSnapshot(
            run_key=key,
            cursor_utc_ns=cursor,
            first_available_at=first,
            last_available_at=last,
            stations=stations,
            events=events,
            protocol=protocol[-100:],
            protocol_status="parsed" if protocol_path.exists() else "not_yet_parsed",
            limitations=(
                "V0.2 开发物理仿真；历史小时背景及可选模型扰动、pvlib/pandapower，非现场校准。"
                "所有曲线均为主站实际收到的同链路遥测，不是独立M3或Oracle真值。"
                if (root / "power_config.json").exists()
                else "V0.1 协议测试模型；固定可用功率，未接入气象或潮流；功率为主站收到的遥测。"
            ),
        )
