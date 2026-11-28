"""Explicit Oracle pair replay using observed telemetry and frozen traffic reports."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from pvcomm.config.models import Contract
from pvcomm.config.pairs import PairConfig
from pvcomm.pipeline.labels import file_sha256
from pvcomm.qc.pairs import VERSION as PAIR_QC_VERSION
from pvcomm.scenario.requests import SCOPE, read_json, safe_file
from pvcomm.web.diagnostic_index import DiagnosticIndexes
from pvcomm.web.runs import Nanoseconds, Point, curve_points

METRICS = {
    1001: ("实际有功遥测", "kW"),
    1003: ("并网点电压遥测", "kV"),
    1004: ("辐照度遥测", "W/m²"),
    1005: ("可用功率遥测", "kW"),
    1006: ("终端已接受目标遥测", "kW"),
}


class PairSummary(Contract):
    pair_key: str
    pair_id: str
    pair_type: str
    status: str
    station_ids: list[str]
    duration_s: int
    data_quality_passed: bool | None
    business_contrast: bool | None
    statistical_match: bool | None
    hard_pair_selected: bool | None


class PairList(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    pairs: list[PairSummary]
    total: int


class PairMemberCurve(Contract):
    member: str
    run_id: str
    status: str
    cursor_utc_ns_exclusive: Nanoseconds
    points: list[Point]
    latest_value: float | None
    last_observation_age_s: float | None
    source: Literal["master_iec104_telemetry"] = "master_iec104_telemetry"
    source_sha256: str


class PairReplay(Contract):
    scope: Literal["Oracle_experiment_management_never_M2_features"] = SCOPE
    pair: PairSummary
    cursor_s: int
    station_id: str
    ioa: int
    metric: str
    unit: str
    members: list[PairMemberCurve]
    traffic_report: dict[str, Any] | None
    traffic_unavailable_reason: str | None
    limitations: str = (
        "Oracle配对诊断；曲线仅主站已收遥测，600条源记录/240点展示预算；"
        "缺失、无效及超过1.5个周期的显示缺口不补值；"
        "固定窗口流量报告仅在游标走完整个分析窗口后显示；"
        "质量与匹配结论为全运行事后诊断；不作为M2模型输入"
    )


def native_path(path: Path) -> Path:
    root = path.resolve()
    value = str(root)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        root = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return root


class PairReplayRepository:
    def __init__(self, experiments: Path | None, archive: Path | None) -> None:
        self.experiments = native_path(experiments) if experiments else None
        self.archive = native_path(archive) if archive else None
        self.indexes = DiagnosticIndexes()
        self.hashes: dict[Path, tuple[tuple[int, int], str]] = {}
        self.lock = threading.RLock()

    @staticmethod
    def file(root: Path, path: Path) -> Path:
        safe_file(root, path)
        parts = path.relative_to(root).parts
        if any((root / Path(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
            raise ValueError("pair replay refuses symbolic links")
        return path

    def directories(self) -> dict[str, Path]:
        result = {}
        for root, pattern in (
            (self.experiments, "experiment_*/queue/*/attempt_*/pair_*"),
            (self.archive, "*/attempt_*/pair_*"),
        ):
            if root is None:
                continue
            for path in root.glob(pattern):
                self.file(root, path)
                if path.is_dir() and (path / "pair-manifest.json").is_file():
                    key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                    result[key] = path
        return result

    def directory(self, key: str) -> Path:
        result = self.directories()
        if key not in result:
            raise FileNotFoundError("unknown retained pair")
        return result[key]

    def summary(self, key: str, path: Path) -> PairSummary:
        config = PairConfig.model_validate(read_json(path, self.file(path, path / "pair.json")))
        ledger = read_json(path, self.file(path, path / "pair-manifest.json"))
        if ledger["pair_id"] != config.pair_id:
            raise ValueError("pair ledger identity changed")
        quality_path = path / "qc" / PAIR_QC_VERSION / "report.json"
        quality = read_json(path, self.file(path, quality_path)) if quality_path.exists() else {}
        report_path = path / "qc/v04-traffic-similarity-v1/report.json"
        report = read_json(path, self.file(path, report_path)) if report_path.exists() else {}
        return PairSummary(
            pair_key=key,
            pair_id=config.pair_id,
            pair_type=config.pair_type,
            status=ledger["status"],
            station_ids=[s.station_id for s in config.normal.simulation.protocol.stations],
            duration_s=config.normal.simulation.protocol.duration_s,
            data_quality_passed=quality.get("data_quality_passed"),
            business_contrast=quality.get("contrast_observed"),
            statistical_match=report.get("matched"),
            hard_pair_selected=report.get("hard_pair_selected"),
        )

    def list(self, offset: int, limit: int) -> PairList:
        paths = sorted(self.directories().items())
        return PairList(
            pairs=[self.summary(key, path) for key, path in paths[offset : offset + limit]],
            total=len(paths),
        )

    def verify_hash(self, path: Path, expected: str) -> None:
        info = path.stat()
        signature = info.st_size, info.st_mtime_ns
        previous = self.hashes.get(path)
        if previous is None or previous[0] != signature:
            digest = file_sha256(path)
            if len(self.hashes) >= 64:
                self.hashes.pop(next(iter(self.hashes)))
            self.hashes[path] = signature, digest
        if self.hashes[path][1] != expected:
            raise ValueError("pair report source checksum changed")

    def replay(self, key: str, station: str, ioa: int, at_s: int | None) -> PairReplay:
        with self.lock, self.indexes.lock:
            path = self.directory(key)
            summary = self.summary(key, path)
            config = PairConfig.model_validate(read_json(path, path / "pair.json"))
            cursor = summary.duration_s if at_s is None else at_s
            if (
                station not in summary.station_ids
                or ioa not in METRICS
                or not 0 <= cursor <= summary.duration_s
            ):
                raise ValueError("invalid pair replay station, metric or cursor")
            ledger = read_json(path, path / "pair-manifest.json")
            if ledger["status"] == "running":
                raise ValueError("pair replay requires a terminated pair")
            members, seen, run_ids = [], set(), set()
            period = next(
                s.telemetry_period_s
                for s in config.normal.simulation.protocol.stations
                if s.station_id == station
            )
            for record in ledger["members"]:
                identity = record["run_id"]
                if (
                    record["run_dir"] != identity
                    or Path(identity).name != identity
                    or record["member"] not in ("normal", "changed")
                    or record["member"] in seen
                    or identity in run_ids
                ):
                    raise ValueError("unsafe or duplicate pair member")
                seen.add(record["member"])
                run_ids.add(identity)
                root = path / identity
                manifest = read_json(path, self.file(path, root / "manifest.json"))
                if manifest["run_id"] != identity or manifest["status"] not in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    raise ValueError("pair member identity or terminal status changed")
                cutoff = manifest["scene_clock"]["start_utc_ns"] + cursor * 1_000_000_000
                source = self.file(path, root / "observed/master_events_master.jsonl")
                index = self.indexes.get(source, identity)
                rows = index.series(station, ioa, cutoff)
                age = (cutoff - rows[-1]["available_at"]) / 1e9 if rows else None
                current = (
                    rows[-1]["value"]
                    if (
                        rows
                        and rows[-1].get("quality_valid") is not False
                        and age is not None
                        and age <= period * 1.5
                    )
                    else None
                )
                members.append(
                    PairMemberCurve(
                        member=record["member"],
                        run_id=identity,
                        status=record["status"],
                        cursor_utc_ns_exclusive=cutoff,
                        points=curve_points(rows, period, 240),
                        latest_value=current,
                        last_observation_age_s=age,
                        source_sha256=index.sha256,
                    )
                )
            report_path = path / "qc/v04-traffic-similarity-v1/report.json"
            report = read_json(path, self.file(path, report_path)) if report_path.exists() else None
            reason = "尚无固定流量相似性报告"
            if report is not None:
                if (
                    report["pair_id"] != summary.pair_id
                    or set(report["summaries"]) != seen
                    or {s["run_id"] for s in report["summaries"].values()} != run_ids
                ):
                    raise ValueError("traffic report belongs to different pair members")
                window_end = max(s["window_s"][1] for s in report["summaries"].values())
                if cursor < window_end:
                    report = None
                    reason = "游标尚未完成固定分析窗口，隐藏事后统计"
                else:
                    for name, digest in report["source_hashes"].items():
                        parts = Path(name).parts
                        if len(parts) == 2 and parts[1] == "primary.pcap":
                            source = path / parts[0] / "raw/pcap/primary.pcap"
                        else:
                            source = path / name
                        self.verify_hash(self.file(path, source), digest)
                    reason = ""
            return PairReplay(
                pair=summary,
                cursor_s=cursor,
                station_id=station,
                ioa=ioa,
                metric=METRICS[ioa][0],
                unit=METRICS[ioa][1],
                members=members,
                traffic_report=report,
                traffic_unavailable_reason=reason or None,
            )


def pair_replay_router(repository: PairReplayRepository) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics/pairs", tags=["Oracle pair replay"])

    @router.get("", response_model=PairList)
    def listing(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)) -> PairList:
        try:
            return repository.list(offset, limit)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail="配对原件不可读取") from exc

    @router.get("/{key}/replay", response_model=PairReplay)
    def replay(
        key: str, station_id: str = "PV01", ioa: int = 1001, at_s: int | None = Query(None, ge=0)
    ) -> PairReplay:
        try:
            return repository.replay(key, station_id, ioa, at_s)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="配对不存在") from exc
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail="配对原件或回放参数无效") from exc

    return router
