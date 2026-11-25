"""Read-only M2 projections. The client cannot select paths or truth fields."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, PlainSerializer

from pvcomm.config.lab import load_v01_config
from pvcomm.config.models import Contract
from pvcomm.config.multisite import MultisiteProtocol
from pvcomm.storage.jsonl import last_complete_record
from pvcomm.web.observation_index import ObservationIndex

Nanoseconds = Annotated[int, PlainSerializer(str, return_type=str, when_used="json")]


def curve_points(rows: list[dict[str, Any]], period_s: int, budget: int) -> list[Point]:
    """Min/max display reduction within a bounded 600-observation window.

    Preserve endpoints and source gaps. This is a UI projection, never an
    export or scientific feature. No future point contributes to a cursor.
    """
    selected = set(range(len(rows)))
    if len(rows) > budget:
        selected = {0, len(rows) - 1}
        width = max(1, (len(rows) - 2 + (budget - 2) // 2 - 1) // ((budget - 2) // 2))
        for start in range(1, len(rows) - 1, width):
            indices = range(start, min(start + width, len(rows) - 1))
            selected.add(min(indices, key=lambda i: rows[i]["value"]))
            selected.add(max(indices, key=lambda i: rows[i]["value"]))
    result, previous = [], 0
    for index in sorted(selected):
        row = rows[index]
        gap = any(
            rows[i]["sim_elapsed_ms"] - rows[i - 1]["sim_elapsed_ms"] > period_s * 1500
            for i in range(previous + 1, index + 1)
        )
        result.append(
            Point(
                available_at=row["available_at"],
                sim_elapsed_ms=row["sim_elapsed_ms"],
                value=row["value"],
                gap_before=gap,
            )
        )
        previous = index
    return result


class RunSummary(Contract):
    run_key: str
    status: str
    source: Literal["real_c104_protocol_simulation"] = "real_c104_protocol_simulation"


class Point(Contract):
    available_at: Nanoseconds
    sim_elapsed_ms: int
    value: float
    gap_before: bool = False


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
    feeder_id: str | None = None
    electrical_parent: str | None = None
    x_m: float | None = None
    y_m: float | None = None
    power_fresh: bool = False
    device_running: bool | None = None
    device_status_available_at: Nanoseconds | None = None


class RegionView(Contract):
    region_id: str
    stations: int
    open_connections: int
    valid_power_stations: int
    capacity_coverage_fraction: float
    observed_power_kw: float | None


class DispatchView(Contract):
    dispatch_id: str
    available_at: Nanoseconds
    regions: list[str]
    requested_kw: float
    allocated_kw: float
    unallocated_kw: float
    stations: int
    positive_confirmations: int
    failed_transmissions: int


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
    regions: list[RegionView] = Field(default_factory=list)
    dispatches: list[DispatchView] = Field(default_factory=list)
    protocol_statistics: dict[str, int] = Field(default_factory=dict)
    limitations: str = "V0.1 协议测试模型；固定可用功率，未接入气象或潮流；功率为主站收到的遥测。"


class RunList(Contract):
    runs: list[RunSummary] = Field(default_factory=list)


class EventsPage(Contract):
    run_key: str
    cursor_utc_ns: Nanoseconds
    events: list[EventView]
    next_before_ns: Nanoseconds | None
    next_before_id: str | None


class ResourceView(Contract):
    scope: Literal["infrastructure_only_not_M2_features"] = "infrastructure_only_not_M2_features"
    ts_utc_ns: Nanoseconds | None = None
    cpu_percent_one_core: float | None = None
    rss_bytes: int | None = None
    disk_read_bytes: int | None = None
    disk_write_bytes: int | None = None
    utc_mapping_drift_ms: float | None = None


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

    def event_page(
        self, key: str, at_ns: int, before: tuple[int, str] | None, limit: int
    ) -> EventsPage:
        with self._lock:
            _, index = self._indexed(key)
            rows = index.rows(at_ns, before=before, limit=limit)
            fields = set(EventView.model_fields)
            more = len(rows) == limit
            return EventsPage(
                run_key=key,
                cursor_utc_ns=at_ns,
                events=[EventView.model_validate({k: r.get(k) for k in fields}) for r in rows],
                next_before_ns=rows[0]["available_at"] if more else None,
                next_before_id=rows[0]["event_id"] if more else None,
            )

    def resources(self, key: str) -> ResourceView:
        directories = self._directories()
        if key not in directories:
            raise FileNotFoundError("unknown run")
        row = last_complete_record(self._safe_file(directories[key] / "resources.jsonl"))
        return ResourceView.model_validate(
            {k: row.get(k) for k in ResourceView.model_fields if k != "scope"} if row else {}
        )

    def _indexed(self, key: str) -> tuple[Path, ObservationIndex]:
        directories = self._directories()
        if key not in directories:
            raise FileNotFoundError("unknown run")
        root = directories[key]
        if key not in self._indexes:
            if len(self._indexes) >= 4:
                oldest = next(iter(self._indexes))
                self._indexes.pop(oldest).connection.close()
                (Path(self._cache.name) / f"{oldest}.sqlite").unlink()
            self._indexes[key] = ObservationIndex(Path(self._cache.name) / f"{key}.sqlite")
        index = self._indexes[key]
        for path in sorted((root / "observed").glob("master_events*.jsonl")):
            index.ingest(self._safe_file(path))
        for path in sorted((root / "observed").glob("master_apdu_events*.jsonl")):
            index.ingest(self._safe_file(path), raw_callbacks=True)
        protocol_path = root / "parsed/tshark-pdml-v2/iec104_apdu.jsonl"
        if protocol_path.exists():
            index.ingest(self._safe_file(protocol_path), protocol=True)
        return root, index

    def _snapshot(self, key: str, at_ns: int | None) -> RunSnapshot:
        root, index = self._indexed(key)
        config = load_v01_config(self._safe_file(root / "scenario.yaml"))
        protocol_path = root / "parsed/tshark-pdml-v2/iec104_apdu.jsonl"
        first, last = index.bounds()
        cursor = min(at_ns, last) if at_ns is not None else last
        statistics = {}
        for direction in ("master_send", "master_receive"):
            records = index.rows(cursor, kind="statistics_" + direction, limit=1)
            statistics[direction + "_callbacks"] = records[0]["count"] if records else 0
            statistics[direction + "_bytes"] = records[0]["bytes"] if records else 0
        stations = []
        for station in config.stations:
            own = []
            for ioa in (1001, 1002, 1003, 1004, 1005, 1006, 1008, 1009, 1010, 1011):
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
            device_status = index.rows(
                cursor, station=station.station_id, kind="master_observed_point", ioa=2001, limit=1
            )
            stations.append(
                StationView(
                    station_id=station.station_id,
                    region_id=station.region_id,
                    common_address=station.common_address,
                    capacity_kw=station.capacity_kw,
                    telemetry_period_s=station.telemetry_period_s,
                    feeder_id=getattr(station, "feeder_id", None),
                    electrical_parent=(
                        next(
                            (
                                s.station_id
                                for s in reversed(config.stations[: config.stations.index(station)])
                                if getattr(s, "feeder_id", None)
                                == getattr(station, "feeder_id", None)
                            ),
                            getattr(station, "feeder_id", None),
                        )
                        if isinstance(config, MultisiteProtocol)
                        else None
                    ),
                    x_m=getattr(station, "x_m", None),
                    y_m=getattr(station, "y_m", None),
                    power_fresh=bool(
                        power
                        and cursor - power[-1]["available_at"]
                        <= (2 * station.telemetry_period_s + 1) * 1_000_000_000
                        and state
                        and state[-1] == "OPEN"
                    ),
                    connection_state=state[-1] if state else None,
                    latest_power_kw=power[-1]["value"] if power else None,
                    power_available_at=power[-1]["available_at"] if power else None,
                    device_running=bool(device_status[-1]["value"]) if device_status else None,
                    device_status_available_at=(
                        device_status[-1]["available_at"] if device_status else None
                    ),
                    measurements=[
                        MeasurementSeries(
                            ioa=ioa,
                            name=name,
                            unit=unit,
                            points=curve_points(
                                [r for r in own if r.get("ioa") == ioa],
                                station.telemetry_period_s,
                                120 if len(config.stations) > 3 else 600,
                            ),
                        )
                        for ioa, name, unit in (
                            (1001, "actual_power", "kW"),
                            (1002, "reactive_power", "kvar"),
                            (1003, "pcc_line_voltage", "kV"),
                            (1004, "current", "A"),
                            (1005, "irradiance", "W/m2"),
                            (1006, "accepted_target", "kW"),
                            (1008, "available_ac_power", "kW"),
                            (1009, "access_line_loading", "%"),
                            (1010, "transformer_loading", "%"),
                            (1011, "line_forward_power", "kW"),
                        )
                    ],
                    power=curve_points(
                        power, station.telemetry_period_s, 120 if len(config.stations) > 3 else 600
                    ),
                )
            )
        event_fields = set(EventView.model_fields)
        events = [
            EventView.model_validate({k: r.get(k) for k in event_fields})
            for r in index.rows(cursor)
        ]
        protocol = [ProtocolView.model_validate(row) for row in index.rows(cursor, kind="protocol")]
        regions = []
        for region_id in sorted({s.region_id for s in stations}):
            members = [s for s in stations if s.region_id == region_id]
            fresh = [s for s in members if s.power_fresh]
            regions.append(
                RegionView(
                    region_id=region_id,
                    stations=len(members),
                    open_connections=sum(s.connection_state == "OPEN" for s in members),
                    valid_power_stations=len(fresh),
                    capacity_coverage_fraction=sum(s.capacity_kw for s in fresh)
                    / sum(s.capacity_kw for s in members),
                    observed_power_kw=sum(s.latest_power_kw or 0 for s in fresh) if fresh else None,
                )
            )
        responses = index.rows(cursor, kind="master_command_response", limit=300)
        dispatches = [
            DispatchView(
                dispatch_id=r["dispatch_id"],
                available_at=r["available_at"],
                regions=r["region_ids"],
                requested_kw=r["requested_kw"],
                allocated_kw=r["allocated_kw"],
                unallocated_kw=r["unallocated_kw"],
                stations=len(r["targets_kw"]),
                positive_confirmations=sum(
                    e.get("dispatch_id") == r["dispatch_id"]
                    and e.get("positive_confirmation") is True
                    for e in responses
                ),
                failed_transmissions=sum(
                    e.get("dispatch_id") == r["dispatch_id"]
                    and e.get("positive_confirmation") is False
                    for e in responses
                ),
            )
            for r in index.rows(cursor, kind="master_dispatch_allocated", limit=30)
        ]
        return RunSnapshot(
            run_key=key,
            cursor_utc_ns=cursor,
            first_available_at=first,
            last_available_at=last,
            stations=stations,
            regions=regions,
            dispatches=dispatches,
            protocol_statistics=statistics,
            events=events,
            protocol=protocol[-100:],
            protocol_status="parsed" if protocol_path.exists() else "not_yet_parsed",
            limitations=(
                "开发物理仿真；历史小时背景及可选空间模型扰动、pvlib/pandapower，非现场校准。"
                "所有曲线均为主站实际收到的同链路遥测，不是独立M3或Oracle真值。"
                if (root / "power_config.json").exists()
                else "V0.1 协议测试模型；固定可用功率，未接入气象或潮流；功率为主站收到的遥测。"
            ),
        )
