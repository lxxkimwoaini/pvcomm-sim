"""RTU-owned physical state and atomic local feeder IPC; never read by master."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pvcomm.clock import ClockOrigin, SceneClock
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import DeviceConfig, V02Config, WeatherConfig
from pvcomm.power.device import Inverter
from pvcomm.power.feeder import FeederNetwork
from pvcomm.power.solar import SolarModel
from pvcomm.protocol.c104_runtime import RecordEvent
from pvcomm.storage.jsonl import MAX_SNAPSHOT_BYTES, last_complete_record
from pvcomm.weather import HistoricalWeather, station_weather


def append_snapshot(path: Path, value: dict[str, Any]) -> None:
    # Append-only IPC works on drvfs too: replace-over-existing may expose a
    # transient missing pathname to a concurrent WSL reader. A partial tail
    # never replaces the last complete record; completed malformed rows fail.
    encoded = json.dumps(value, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("IPC snapshot exceeds maximum size")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()


def read_snapshot(path: Path) -> dict[str, Any] | None:
    return last_complete_record(path)


def prepare_weather(destination: Path, config: WeatherConfig, duration_s: int) -> None:
    source = Path(config.directory).resolve()
    weather = HistoricalWeather(source / "weather.json", config)
    weather.sample(duration_s + 120)
    metadata = json.loads((source / "source.json").read_text(encoding="utf-8"))
    if metadata["canonical_sha256"] != config.sha256:
        raise ValueError("weather provenance disagrees with configured hash")
    raw = (source / "raw.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["files"]["raw.json"]["sha256"]:
        raise ValueError("raw weather provenance hash mismatch")
    canonical = json.dumps(
        {k: v for k, v in json.loads(raw).items() if k != "times"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if hashlib.sha256(canonical).hexdigest() != config.sha256:
        raise ValueError("canonical weather differs from retained raw source")
    destination.mkdir(parents=True)
    for name in (
        "raw.json",
        "weather.json",
        "source.json",
        "api.html",
        "referencing.html",
        "policy.html",
    ):
        shutil.copyfile(source / name, destination / name)


def weather_directory(root: Path, config: V02Config | V03Config, station_id: str) -> Path:
    base = root / "truth/weather"
    return (
        base / station_id
        if isinstance(config, V03Config) and station_id in config.weather_by_station
        else base
    )


def prepare_power(root: Path, config: V02Config | V03Config) -> None:
    prepare_weather(root / "truth/weather", config.weather, config.protocol.duration_s)
    if isinstance(config, V03Config):
        for station_id, weather in config.weather_by_station.items():
            prepare_weather(
                root / "truth/weather" / station_id, weather, config.protocol.duration_s
            )
    (root / "power_config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    (root / "truth/ipc").mkdir()


class PhysicalStationState:
    """Owned exclusively by one RTU; accept is invoked by real protocol callback."""

    def __init__(
        self,
        root: Path,
        config: V02Config | V03Config,
        device: DeviceConfig,
        origin: ClockOrigin,
        record_event: RecordEvent,
    ) -> None:
        self.root = root
        self.config = config
        self.device = device
        self.origin = origin
        self.scene_clock = (
            SceneClock(root / "scene.json") if isinstance(config, V03Config) else None
        )
        self._record = record_event
        self._applied_operations = 0
        self._operations = (
            [op for op in config.device_operations if op.station_id == device.station_id]
            if isinstance(config, V03Config)
            else []
        )
        weather_config = station_weather(config, device.station_id)
        self.weather = HistoricalWeather(
            weather_directory(root, config, device.station_id) / "weather.json", weather_config
        )
        self.solar = SolarModel(device, weather_config)
        now = time.monotonic()
        available = self.solar.calculate(self.weather.sample(self.elapsed(now)))["p_avail_kw"]
        self.inverter = Inverter(
            device,
            device.initial_setpoint_kw,
            min(available, device.initial_setpoint_kw),
            last_update_mono=self.elapsed(now) if self.scene_clock is not None else now,
        )
        self.sample: dict[str, Any] = {}
        self.advance(now)

    def elapsed(self, now: float) -> float:
        return (
            self.scene_clock.elapsed(int(now * 1e9))
            if self.scene_clock is not None
            else (int(now * 1e9) - self.origin.ts_mono_ns) / 1e9
        )

    @property
    def accepted_setpoint_kw(self) -> float:
        return self.inverter.accepted_setpoint_kw

    @property
    def actual_kw(self) -> float:
        return self.inverter.actual_kw

    @property
    def command_count(self) -> int:
        return self.inverter.command_count

    def advance(self, now: float) -> None:
        elapsed = self.elapsed(now)
        for operation in self._operations[self._applied_operations :]:
            if elapsed < operation.at_s:
                break
            # A local operation changes enablement only, never a remote setpoint,
            # connectivity or fault status. It remains separate Oracle evidence.
            self.inverter.config = self.inverter.config.model_copy(
                update={"enabled": operation.enabled}
            )
            self._applied_operations += 1
            self._record(
                "terminal_local_device_operation",
                {**operation.model_dump(), "applied_scene_s": elapsed},
            )
        weather = self.weather.sample(elapsed)
        solar = self.solar.calculate(weather)
        self.inverter.advance(
            solar["p_avail_kw"], self.elapsed(now) if self.scene_clock is not None else now
        )
        values = {
            **{
                k: v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in asdict(weather).items()
            },
            **solar,
            "station_id": self.device.station_id,
            "p_actual_kw": self.actual_kw,
            "q_actual_kvar": self.inverter.reactive_kvar,
            "p_set_accepted_kw": self.accepted_setpoint_kw,
            "running": self.inverter.running,
            "connected": self.device.connected,
            "faulted": self.device.faulted,
            "enabled": self.inverter.config.enabled,
            "command_count": self.command_count,
            "model_mono_ns": int(now * 1e9),
            "constraint_reason": self.inverter.constraint_reason,
            "source": "pvlib_pvwatts_inverter_v02",
        }
        self.sample = values
        append_snapshot(self.root / f"truth/ipc/{self.device.station_id}.jsonl", values)
        self._record("electrical_state", values)

    def accept(self, target: float, now: float) -> bool:
        # Account for the OLD target up to receipt; weather/model time is real 1x.
        self.advance(now)
        accepted = self.inverter.accept(target)
        if accepted:
            self._record(
                "terminal_model_target_accepted",
                {
                    "station_id": self.device.station_id,
                    "target_kw": target,
                    "ioa": 3001,
                    "command_count": self.command_count,
                },
            )
            self.advance(now)
        return accepted

    def telemetry(self) -> dict[int, float | bool]:
        voltage_file = self.root / f"truth/ipc/{self.device.station_id}-pcc.jsonl"
        voltage = read_snapshot(voltage_file)
        if voltage is None:
            raise RuntimeError("physical feeder measurement is not ready")
        if time.monotonic_ns() - voltage["solved_mono_ns"] > 10_000_000_000:
            raise RuntimeError("physical feeder measurement is stale")
        readings: dict[int, float | bool] = {
            1002: self.inverter.reactive_kvar,
            1003: voltage["voltage_kv"],
            1004: voltage["current_a"],
            1005: self.sample["ghi_w_m2"],
            1008: self.sample["p_avail_kw"],
            2001: self.inverter.running,
        }
        if isinstance(self.config, V03Config) and self.config.contract_revision >= 2:
            readings.update(
                {
                    1009: voltage["line_loading_percent"],
                    1010: voltage["transformer_loading_percent"],
                    1011: voltage["line_p_from_kw"],
                }
            )
        return readings


class FeederRuntime:
    def __init__(
        self, root: Path, config: V02Config | V03Config, record_event: RecordEvent
    ) -> None:
        self.root, self.config, self.record = root, config, record_event
        self.model = FeederNetwork(config)
        self.last_solve = 0.0
        self.last_commands: tuple[int, ...] = ()

    def step(self) -> bool:
        paths = [self.root / f"truth/ipc/{d.station_id}.jsonl" for d in self.config.devices]
        snapshots = [read_snapshot(p) for p in paths]
        if any(s is None for s in snapshots):
            return False
        states = {
            d.station_id: sample
            for d, sample in zip(self.config.devices, snapshots, strict=True)
            if sample is not None
        }
        now = time.monotonic()
        commands = tuple(s["command_count"] for s in states.values())
        if commands == self.last_commands and now - self.last_solve < self.config.powerflow_step_s:
            return True
        if any(now * 1e9 - s["model_mono_ns"] > 3_000_000_000 for s in states.values()):
            raise RuntimeError("stale RTU injection: cannot solve a valid feeder sample")
        try:
            results = self.model.solve(states)
        except Exception as exc:
            self.record("terminal_powerflow_failed", {"error": str(exc), "converged": False})
            raise
        stamp = time.monotonic_ns()
        if isinstance(self.config, V03Config):
            for feeder_id, diagnostic in self.model.diagnostic_rows.items():
                self.record(
                    "terminal_feeder_diagnostics",
                    {
                        "feeder_id": feeder_id,
                        "solved_mono_ns": stamp,
                        "solve_duration_ms": (stamp / 1e9 - now) * 1000,
                        "snapshot_span_ms": (
                            max(s["model_mono_ns"] for s in states.values())
                            - min(s["model_mono_ns"] for s in states.values())
                        )
                        / 1e6,
                        "calculation_valid": True,
                        **diagnostic,
                        "reverse_flow": diagnostic["grid_p_kw"] < 0,
                        "electrical_limit_violation": diagnostic["bus_min_pu"] < 0.9
                        or diagnostic["bus_max_pu"] > 1.1
                        or diagnostic["line_max_loading_percent"] > 100
                        or diagnostic["transformer_loading_percent"] > 100,
                    },
                )
        for station, result in results.items():
            row = {
                **result,
                "solved_mono_ns": stamp,
                "converged": True,
                "station_id": station,
                "injection_mono_ns": states[station]["model_mono_ns"],
                "p_actual_kw": states[station]["p_actual_kw"],
                "q_actual_kvar": states[station]["q_actual_kvar"],
                "source": "pandapower_three_site_v02",
            }
            append_snapshot(self.root / f"truth/ipc/{station}-pcc.jsonl", row)
            self.record("terminal_powerflow_solved", row)
        self.last_commands, self.last_solve = commands, now
        return True
