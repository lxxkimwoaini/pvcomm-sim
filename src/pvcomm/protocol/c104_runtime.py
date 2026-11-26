"""c104 2.2.1 runtime adapters for the V0.1 local laboratory run.

The c104 extension validates callback annotations at runtime.  The adapter
therefore imports c104 only inside lab execution and assigns the exact
annotations required by the extension after defining callbacks.  This keeps
the ordinary Windows/CI environment independent of the optional protocol
dependency while retaining strict callback contracts in the actual run.
"""

from __future__ import annotations

import importlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pvcomm.config.lab import V01StationConfig
from pvcomm.domain.command import CommandResult, ObservationUnavailable
from pvcomm.protocol.confirmation import ConfirmationTracker, confirmations
from pvcomm.protocol.terminal_faults import TerminalFaultRuntime

RecordEvent = Callable[[str, dict[str, object]], None]
RecordRaw = Callable[[str, str, bytes], None]


def _set_callback_annotations(
    callback: Callable[..., object], annotations: dict[str, object]
) -> None:
    callback.__annotations__ = annotations


class StationModel(Protocol):
    @property
    def accepted_setpoint_kw(self) -> float: ...
    @property
    def actual_kw(self) -> float: ...
    def advance(self, now: float) -> None: ...
    def accept(self, target: float, now: float) -> bool: ...
    def telemetry(self) -> dict[int, float | bool]: ...


@dataclass
class StationState:
    config: V01StationConfig
    accepted_setpoint_kw: float
    actual_kw: float
    command_count: int = 0
    last_update_mono: float = 0.0

    def advance(self, now: float) -> None:
        if now < self.last_update_mono:
            raise ValueError("device time cannot go backwards")
        target = min(self.config.available_kw, self.accepted_setpoint_kw)
        step = self.config.ramp_kw_per_s * (now - self.last_update_mono)
        self.actual_kw += max(-step, min(step, target - self.actual_kw))
        self.last_update_mono = now

    def accept(self, target: float, now: float) -> bool:
        if not math.isfinite(target) or not 0 <= target <= self.config.available_kw:
            return False
        # Advance under the OLD setpoint up to actual command receipt. Time
        # before receipt cannot be credited to execution of the new target.
        self.advance(now)
        self.accepted_setpoint_kw = target
        self.command_count += 1
        return True

    def telemetry(self) -> dict[int, float | bool]:
        return {1002: self.config.reactive_kvar, 1003: self.config.voltage_kv, 2001: True}


class C104StationEndpoint:
    """One real c104 server process model and one common address."""

    def __init__(
        self,
        config: V01StationConfig,
        host: str,
        record_event: RecordEvent,
        record_raw: RecordRaw,
        state: StationModel | None = None,
        logical_elapsed: Callable[[float], float] | None = None,
        fault: TerminalFaultRuntime | None = None,
    ) -> None:
        c104 = importlib.import_module("c104")
        self._c104 = c104
        self.config = config
        self._record_event = record_event
        self._record_raw = record_raw
        self._lock = threading.RLock()
        self._logical_elapsed = logical_elapsed
        self.fault = fault
        self._reporting_mask: frozenset[int] = frozenset()
        self._active = False
        self._next_report_s = float(getattr(config, "telemetry_phase_s", 0))
        self._last_sent_power: float | None = None
        self._last_sent_running: bool | None = None
        now = time.monotonic()
        self.state: StationModel = state or StationState(
            config=config,
            accepted_setpoint_kw=config.initial_setpoint_kw,
            actual_kw=min(config.available_kw, config.initial_setpoint_kw),
            last_update_mono=now,
        )
        self.server = c104.Server(ip=host, port=config.port, tick_rate_ms=100)
        station = self.server.add_station(common_address=config.common_address)
        if station is None:
            raise RuntimeError(f"failed to add station {config.station_id}")
        self.station = station
        self.power_point = station.add_point(
            io_address=1001,
            type=c104.Type.M_ME_NC_1,
            report_ms=config.telemetry_period_s * 1000,
        )
        self.reactive_point = station.add_point(
            io_address=1002,
            type=c104.Type.M_ME_NC_1,
            report_ms=config.telemetry_period_s * 1000,
        )
        self.voltage_point = station.add_point(
            io_address=1003,
            type=c104.Type.M_ME_NC_1,
            report_ms=config.telemetry_period_s * 1000,
        )
        self.accepted_setpoint_point = station.add_point(io_address=1006, type=c104.Type.M_ME_NC_1)
        self.status_point = station.add_point(
            io_address=2001,
            type=c104.Type.M_SP_NA_1,
            report_ms=config.telemetry_period_s * 1000,
        )
        self.command_point = station.add_point(
            io_address=3001,
            type=c104.Type.C_SE_NC_1,
            command_mode=c104.CommandMode.DIRECT,
        )
        if any(
            point is None
            for point in (
                self.power_point,
                self.reactive_point,
                self.voltage_point,
                self.accepted_setpoint_point,
                self.status_point,
                self.command_point,
            )
        ):
            raise RuntimeError(f"failed to create point table for {config.station_id}")
        self.power_point.value = self.state.actual_kw
        self.reactive_point.value = config.reactive_kvar
        self.voltage_point.value = config.voltage_kv
        self.accepted_setpoint_point.value = self.state.accepted_setpoint_kw
        self.status_point.value = True
        self.extra_points: dict[int, Any] = {}
        for ioa in self.state.telemetry():
            if ioa in (1002, 1003, 2001):
                continue
            self.extra_points[ioa] = station.add_point(
                io_address=ioa,
                type=c104.Type.M_ME_NC_1,
                report_ms=config.telemetry_period_s * 1000,
            )
            if self.extra_points[ioa] is None:
                raise RuntimeError(f"failed to bind physical point {ioa}")
        self._refresh_telemetry()
        self._install_callbacks()
        if logical_elapsed is not None:
            for point in (
                self.power_point,
                self.reactive_point,
                self.voltage_point,
                self.status_point,
                *self.extra_points.values(),
            ):
                point.report_ms = 0
        if fault is not None:
            fault.bind(self.set_reporting_mask)

    def measurement_points(self) -> dict[int, Any]:
        return {
            1001: self.power_point,
            1002: self.reactive_point,
            1003: self.voltage_point,
            1006: self.accepted_setpoint_point,
            2001: self.status_point,
            **self.extra_points,
        }

    def set_reporting_mask(self, suppressed: frozenset[int]) -> list[int]:
        """Suspend registration as well as explicit reports so GI cannot bypass a fault.

        Point definitions and current physical values remain in this adapter;
        restoration re-registers them through c104, never writes wire bytes.
        """
        with self._lock:
            points = self.measurement_points()
            selected = suppressed & points.keys()
            for ioa in selected - self._reporting_mask:
                if not self.station.remove_point(io_address=ioa):
                    raise RuntimeError(f"c104 could not suspend reporting point {ioa}")
            restored = self._reporting_mask - selected
            for ioa in restored:
                previous = points[ioa]
                point = self.station.add_point(io_address=ioa, type=previous.type, report_ms=0)
                if point is None:
                    raise RuntimeError(f"c104 could not restore reporting point {ioa}")
                point.value = previous.value
                if ioa in self.extra_points:
                    self.extra_points[ioa] = point
                else:
                    attr = {
                        1001: "power_point",
                        1002: "reactive_point",
                        1003: "voltage_point",
                        1006: "accepted_setpoint_point",
                        2001: "status_point",
                    }[ioa]
                    setattr(self, attr, point)
            self._reporting_mask = frozenset(selected)
            if restored:
                self._install_callbacks()
            registered = sorted(p.io_address for p in self.station.points)
            if any(ioa in registered for ioa in selected) or any(
                ioa not in registered for ioa in points.keys() - selected
            ):
                raise RuntimeError("c104 reporting registry readback mismatch")
            return registered

    def _refresh_telemetry(self) -> None:
        points = {
            1002: self.reactive_point,
            1003: self.voltage_point,
            2001: self.status_point,
            **self.extra_points,
        }
        for ioa, value in self.state.telemetry().items():
            points[ioa].value = value

    def tick(self) -> None:
        with self._lock:
            now = time.monotonic()
            self.state.advance(now)
            self.power_point.value = self.state.actual_kw
            self._refresh_telemetry()
            if self._logical_elapsed is not None and self._active:
                elapsed = self._logical_elapsed(now)
                periodic = elapsed >= self._next_report_s and elapsed > 0
                event = self._last_sent_power is not None and abs(
                    self.state.actual_kw - self._last_sent_power
                ) >= self.config.capacity_kw * float(
                    getattr(self.config, "deadband_fraction", 0.01)
                )
                running = bool(self.status_point.value)
                state_changed = (
                    self._last_sent_running is not None and running != self._last_sent_running
                )
                if periodic or event or state_changed:
                    cause = self._c104.Cot.PERIODIC if periodic else self._c104.Cot.SPONTANEOUS
                    for point in (
                        self.power_point,
                        self.reactive_point,
                        self.voltage_point,
                        self.status_point,
                        *self.extra_points.values(),
                    ):
                        if point.io_address not in self._reporting_mask:
                            point.transmit(cause=cause)
                        else:
                            self._record_event(
                                "terminal_report_suppressed",
                                {
                                    "station_id": self.config.station_id,
                                    "ioa": point.io_address,
                                    "reason": "periodic" if periodic else "event",
                                },
                            )
                    self._last_sent_power, self._last_sent_running = self.state.actual_kw, running
                    self._record_event(
                        "terminal_telemetry_report",
                        {
                            "station_id": self.config.station_id,
                            "reason": "periodic"
                            if periodic
                            else "state_change"
                            if state_changed
                            else "power_deadband",
                            "scene_elapsed_s": elapsed,
                        },
                    )
                if periodic:
                    while self._next_report_s <= elapsed:
                        self._next_report_s += self.config.telemetry_period_s

    def _install_callbacks(self) -> None:
        c104 = self._c104

        def on_command(point: Any, previous_info: Any, message: Any) -> Any:
            target = float(point.value)
            if self.fault is not None:
                self.fault.delay_response()
            with self._lock:
                accepted = self.state.accept(target, time.monotonic())
                if accepted:
                    self.accepted_setpoint_point.value = target
                    if self.fault is not None:
                        self.fault.accepted()
                else:
                    point.info = previous_info
            self._record_event(
                "terminal_command_accepted" if accepted else "terminal_command_rejected",
                {
                    "station_id": self.config.station_id,
                    "common_address": self.config.common_address,
                    "ioa": point.io_address,
                    "target_kw": target,
                    "previous_value": getattr(previous_info, "value", None),
                    "cot": message.cot.name,
                    "accepted_setpoint_kw": self.state.accepted_setpoint_kw,
                },
            )
            if accepted:
                if 1006 not in self._reporting_mask:
                    self.accepted_setpoint_point.transmit(cause=c104.Cot.SPONTANEOUS)
                return c104.ResponseState.SUCCESS
            return c104.ResponseState.FAILURE

        _set_callback_annotations(
            on_command,
            {
                "point": c104.Point,
                "previous_info": c104.Information,
                "message": c104.IncomingMessage,
                "return": c104.ResponseState,
            },
        )
        self.command_point.on_receive(on_command)

        def on_before_power_transmit(point: Any) -> None:
            now = time.monotonic()
            with self._lock:
                if self._logical_elapsed is None:
                    self.state.advance(now)
                point.value = self.state.actual_kw
                self._refresh_telemetry()
                actual = self.state.actual_kw
                accepted = self.state.accepted_setpoint_kw
            self._record_event(
                "electrical_state",
                {
                    "station_id": self.config.station_id,
                    "p_actual_kw": actual,
                    "p_avail_kw": self.config.available_kw
                    if isinstance(self.state, StationState)
                    else self.extra_points[1008].value,
                    "p_set_accepted_kw": accepted,
                    "source": "c104_periodic_transmit",
                },
            )

        _set_callback_annotations(on_before_power_transmit, {"point": c104.Point, "return": None})
        self.power_point.on_before_auto_transmit(on_before_power_transmit)
        self.power_point.on_before_read(on_before_power_transmit)

        def on_before_status_transmit(point: Any) -> None:
            with self._lock:
                self._refresh_telemetry()

        _set_callback_annotations(on_before_status_transmit, {"point": c104.Point, "return": None})
        for point in (
            self.reactive_point,
            self.voltage_point,
            self.status_point,
            *self.extra_points.values(),
        ):
            point.on_before_auto_transmit(on_before_status_transmit)
            point.on_before_read(on_before_status_transmit)

        def on_server_send(server: Any, data: bytes) -> None:
            self._record_raw("rtu_send", self.config.station_id, data)

        _set_callback_annotations(
            on_server_send, {"server": c104.Server, "data": bytes, "return": None}
        )
        self.server.on_send_raw(on_server_send)

        def on_server_receive(server: Any, data: bytes) -> None:
            self._record_raw("rtu_receive", self.config.station_id, data)

        _set_callback_annotations(
            on_server_receive, {"server": c104.Server, "data": bytes, "return": None}
        )
        self.server.on_receive_raw(on_server_receive)

    def start(self) -> None:
        self.server.start()
        self._active = True
        self._record_event(
            "rtu_started",
            {
                "station_id": self.config.station_id,
                "common_address": self.config.common_address,
                "port": self.config.port,
            },
        )

    def stop(self) -> None:
        self._active = False
        self._record_event("rtu_stopping", {"station_id": self.config.station_id})
        self.server.stop()
        self._record_event("rtu_stopped", {"station_id": self.config.station_id})


class C104Master:
    """One real c104 client controlling the configured RTU endpoints."""

    def __init__(
        self,
        host: str,
        stations: tuple[V01StationConfig, ...],
        record_event: RecordEvent,
        record_raw: RecordRaw,
        station_hosts: dict[str, str] | None = None,
        managed_lifecycle: bool = False,
    ) -> None:
        c104 = importlib.import_module("c104")
        self._c104 = c104
        self._host = host
        self._station_hosts = station_hosts or {}
        self._stations = stations
        self._record_event = record_event
        self._record_raw = record_raw
        self._managed_lifecycle = managed_lifecycle
        self._lifecycle_condition = threading.Condition()
        self._callback_states: dict[str, str] = {}
        self.client = c104.Client(tick_rate_ms=100, command_timeout_ms=3000)
        self._connections: dict[str, Any] = {}
        self._remote_stations: dict[str, Any] = {}
        self._command_lock = threading.Lock()
        self._station_locks = {s.station_id: threading.Lock() for s in stations}
        self._command_sequence = 0
        self._observations: dict[tuple[str, int], tuple[float, float]] = {}
        self._invalid_observations: set[tuple[str, int]] = set()
        self._observation_lock = threading.Lock()
        self._confirmations = ConfirmationTracker()

    def _install_connection_callbacks(
        self, station_config: V01StationConfig, connection: Any
    ) -> None:
        c104 = self._c104

        def on_state_change(connection: Any, state: Any) -> None:
            self._confirmations.state(station_config.station_id, state.name)
            self._record_event(
                "master_connection_state",
                {"station_id": station_config.station_id, "state": state.name},
            )
            with self._lifecycle_condition:
                self._callback_states[station_config.station_id] = state.name
                self._lifecycle_condition.notify_all()

        _set_callback_annotations(
            on_state_change,
            {"connection": c104.Connection, "state": c104.ConnectionState, "return": None},
        )
        connection.on_state_change(on_state_change)

        def on_receive_raw(connection: Any, data: bytes) -> None:
            received_ns = time.monotonic_ns()
            self._record_raw("master_receive", station_config.station_id, data)
            for item in confirmations(data):
                task_id = self._confirmations.observe(station_config.station_id, item, received_ns)
                self._record_event(
                    "master_command_confirmation",
                    {
                        "station_id": station_config.station_id,
                        "task_id": task_id,
                        "common_address": item.common_address,
                        "ioa": item.ioa,
                        "target_kw": item.target_kw,
                        "positive_confirmation": item.positive,
                        "matched": task_id is not None,
                        "received_mono_ns": received_ns,
                        "data_hex": item.data_hex,
                        "connection_epoch": self._confirmations.epochs.get(
                            station_config.station_id, 0
                        ),
                    },
                )

        _set_callback_annotations(
            on_receive_raw,
            {"connection": c104.Connection, "data": bytes, "return": None},
        )
        connection.on_receive_raw(on_receive_raw)

        def on_send_raw(connection: Any, data: bytes) -> None:
            self._record_raw("master_send", station_config.station_id, data)

        _set_callback_annotations(
            on_send_raw,
            {"connection": c104.Connection, "data": bytes, "return": None},
        )
        connection.on_send_raw(on_send_raw)

    def start(self) -> None:
        c104 = self._c104
        for station_config in self._stations:
            connection = self.client.add_connection(
                ip=self._station_hosts.get(station_config.station_id, self._host),
                port=station_config.port,
                init=c104.Init.NONE if self._managed_lifecycle else c104.Init.ALL,
            )
            if connection is None:
                raise RuntimeError(f"failed to create connection for {station_config.station_id}")
            self._connections[station_config.station_id] = connection
            parameters = connection.protocol_parameters
            self._record_event(
                "master_protocol_parameters",
                {
                    "station_id": station_config.station_id,
                    **{
                        name: getattr(parameters, name)
                        for name in (
                            "send_window_size",
                            "receive_window_size",
                            "connection_timeout",
                            "message_timeout",
                            "confirm_interval",
                            "keep_alive_interval",
                        )
                    },
                },
            )
            self._install_connection_callbacks(station_config, connection)
            station = connection.add_station(common_address=station_config.common_address)
            if station is None:
                raise RuntimeError("failed to preconfigure master station")
            self._remote_stations[station_config.station_id] = station
            self._install_point_callbacks(station_config, station)
        self.client.start()
        for connection in self._connections.values():
            connection.connect()

    def wait_until_open(self, timeout_s: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if all(
                connection.state == self._c104.ConnectionState.OPEN
                for connection in self._connections.values()
            ):
                return
            time.sleep(0.05)
        states = {
            station_id: connection.state.name
            for station_id, connection in self._connections.items()
        }
        raise TimeoutError(f"IEC 104 connections did not open: {states}")

    def _install_point_callbacks(self, station_config: V01StationConfig, station: Any) -> None:
        c104 = self._c104
        for io_address, point_type in (
            (1001, c104.Type.M_ME_NC_1),
            (1002, c104.Type.M_ME_NC_1),
            (1003, c104.Type.M_ME_NC_1),
            (1004, c104.Type.M_ME_NC_1),
            (1005, c104.Type.M_ME_NC_1),
            (1008, c104.Type.M_ME_NC_1),
            (1009, c104.Type.M_ME_NC_1),
            (1010, c104.Type.M_ME_NC_1),
            (1011, c104.Type.M_ME_NC_1),
            (1006, c104.Type.M_ME_NC_1),
            (2001, c104.Type.M_SP_NA_1),
        ):
            point = station.get_point(io_address=io_address)
            if point is None:
                point = station.add_point(io_address=io_address, type=point_type)
            if point is None:
                raise RuntimeError(
                    f"failed to bind point {io_address} for {station_config.station_id}"
                )

            def on_receive(
                point: Any,
                previous_info: Any,
                message: Any,
            ) -> Any:
                quality = point.info.quality
                value = float(point.value)
                valid = quality is not None and quality.is_good() and math.isfinite(value)
                self._publish_observation(
                    station_config.station_id,
                    point.io_address,
                    value,
                    bool(valid),
                    message.cot.name,
                )
                return c104.ResponseState.SUCCESS

            _set_callback_annotations(
                on_receive,
                {
                    "point": c104.Point,
                    "previous_info": c104.Information,
                    "message": c104.IncomingMessage,
                    "return": c104.ResponseState,
                },
            )
            point.on_receive(on_receive)
        command_point = station.get_point(io_address=3001)
        if command_point is None:
            command_point = station.add_point(
                io_address=3001, type=c104.Type.C_SE_NC_1, command_mode=c104.CommandMode.DIRECT
            )
        if command_point is None:
            raise RuntimeError(f"failed to bind command point for {station_config.station_id}")

    def interrogate_all(self) -> None:
        for station_config in self._stations:
            connection = self._connections[station_config.station_id]
            result = connection.interrogation(
                common_address=station_config.common_address,
                cause=self._c104.Cot.ACTIVATION,
                wait_for_response=True,
            )
            self._record_event(
                "master_interrogation",
                {
                    "station_id": station_config.station_id,
                    "common_address": station_config.common_address,
                    "success": result,
                },
            )
            if self._managed_lifecycle:
                # Init.ALL executes synchronous GI/clock waits on c104's raw
                # callback scheduler. Explicit initialization here leaves that
                # scheduler available to timestamp actual callbacks promptly.
                synced = connection.clock_sync(
                    common_address=station_config.common_address, wait_for_response=True
                )
                self._record_event(
                    "master_clock_synchronization",
                    {"station_id": station_config.station_id, "success": synced},
                )
                if not synced:
                    raise RuntimeError(f"clock synchronization failed: {station_config.station_id}")
            if not result:
                raise RuntimeError(f"interrogation failed for {station_config.station_id}")

    def interrogate(
        self, station_config: V01StationConfig, *, dispatch_id: str, raise_on_failure: bool = True
    ) -> bool:
        """Runtime GI, separate from startup clock synchronization and recovery."""
        station_id = station_config.station_id
        with self._station_locks[station_id]:
            self._record_event(
                "master_interrogation_requested",
                {
                    "station_id": station_id,
                    "common_address": station_config.common_address,
                    "dispatch_id": dispatch_id,
                },
            )
            result = bool(
                self._connections[station_id].interrogation(
                    common_address=station_config.common_address,
                    cause=self._c104.Cot.ACTIVATION,
                    wait_for_response=True,
                )
            )
            self._record_event(
                "master_interrogation_result",
                {"station_id": station_id, "dispatch_id": dispatch_id, "success": result},
            )
            if not result and raise_on_failure:
                raise RuntimeError(f"runtime interrogation failed for {station_id}")
            return result

    def recover(self, station_id: str, *, raise_on_failure: bool = True) -> bool:
        connection = self._connections[station_id]
        deadline = time.monotonic() + 15
        while connection.state != self._c104.ConnectionState.OPEN:
            if time.monotonic() >= deadline:
                self._record_event(
                    "master_recovery_connection_result",
                    {"station_id": station_id, "outcome": "connection_timeout", "success": False},
                )
                if raise_on_failure:
                    raise TimeoutError(f"station did not recover: {station_id}")
                return False
            time.sleep(0.05)
        station = next(s for s in self._stations if s.station_id == station_id)
        result = connection.interrogation(
            common_address=station.common_address,
            cause=self._c104.Cot.ACTIVATION,
            wait_for_response=True,
        )
        self._record_event(
            "master_recovery_interrogation",
            {
                "station_id": station_id,
                "success": result,
            },
        )
        if not result and raise_on_failure:
            raise RuntimeError("recovery interrogation failed")
        return bool(result)

    def suspend_for_maintenance(self, station_id: str) -> None:
        """Drain actual STOPDT before an authorized endpoint shutdown.

        A raw server.stop can abandon an in-flight S frame behind its closed
        reader. This handshake is only for planned maintenance, never a fault.
        """
        connection = self._connections[station_id]
        if not connection.mute():
            raise RuntimeError(f"maintenance STOPDT failed: {station_id}")
        self._wait_callback_states([station_id], "OPEN_MUTED")
        connection.disconnect()
        self._wait_callback_states([station_id], "CLOSED")
        self._record_event("master_maintenance_connection_closed", {"station_id": station_id})

    def resume_connection(self, station_id: str) -> None:
        self._connections[station_id].connect()
        self._record_event("master_maintenance_connection_requested", {"station_id": station_id})

    def send_setpoint(
        self,
        station_config: V01StationConfig,
        target_kw: float,
        action: str,
        *,
        dispatch_id: str | None = None,
        raise_on_failure: bool = True,
    ) -> CommandResult:
        # IEC 104 does not carry task_id. Serialize local callers, including
        # repeated values/actions, until the protocol confirmation returns.
        with self._station_locks[station_config.station_id]:
            result = self._send_setpoint(station_config, target_kw, action, dispatch_id=dispatch_id)
            if raise_on_failure and result.outcome != "accepted":
                raise RuntimeError(f"command {result.outcome} for {station_config.station_id}")
            return result

    def _send_setpoint(
        self,
        station_config: V01StationConfig,
        target_kw: float,
        action: str,
        *,
        dispatch_id: str | None = None,
    ) -> CommandResult:
        if not math.isfinite(target_kw) or target_kw < 0:
            raise ValueError("setpoint must be finite and nonnegative")
        # c104's pybind setter distinguishes Python int from native float.
        # Both are valid numeric kW inputs to this application API.
        target_kw = float(target_kw)
        station = self._remote_stations[station_config.station_id]
        point = station.get_point(io_address=3001)
        if point is None:
            raise RuntimeError(f"command point missing for {station_config.station_id}")
        with self._observation_lock:
            sample = self._observations.get((station_config.station_id, 1001))
            valid = (station_config.station_id, 1001) not in self._invalid_observations
        current_kw = (
            sample[0]
            if sample is not None
            and valid
            and 0 <= time.monotonic() - sample[1] <= station_config.telemetry_period_s * 2 + 1
            else None
        )
        distance_kw = (
            abs(target_kw - current_kw)
            if current_kw is not None
            else max(target_kw, abs(station_config.capacity_kw - target_kw))
        )
        execution_s = max(
            10.0,
            min(
                60.0,
                distance_kw / station_config.ramp_kw_per_s
                + 2 * station_config.telemetry_period_s
                + 1,
            ),
        )
        point.value = target_kw
        with self._command_lock:
            self._command_sequence += 1
            task_id = f"{station_config.station_id}-{self._command_sequence:09d}-{action}"
        sent_ns, sent_utc_ns = time.monotonic_ns(), time.time_ns()
        outcome = self._confirmations.begin(
            station_config.station_id,
            task_id,
            station_config.common_address,
            target_kw,
            sent_ns,
            sent_ns + 3_000_000_000,
        )
        epoch = self._confirmations.epochs.get(station_config.station_id, 0)
        self._record_event(
            "master_command_sent" if outcome is None else "master_command_not_sent",
            {
                "station_id": station_config.station_id,
                "ioa": 3001,
                "target_kw": target_kw,
                "action": action,
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "deadline_ms": 3000,
                "execution_deadline_ms": math.ceil(execution_s * 1000),
                "sent_mono_ns": sent_ns if outcome is None else None,
                "sent_utc_ns": sent_utc_ns if outcome is None else None,
                "connection_epoch": epoch,
                "current_power_kw": current_kw,
                "execution_basis": "fresh_observed_power"
                if current_kw is not None
                else "configured_capacity_bound",
            },
        )
        stack_result = None
        positive = None
        if outcome is None:
            stack_result = bool(point.transmit(cause=self._c104.Cot.ACTIVATION))
            outcome, positive = self._confirmations.finish(station_config.station_id)
        self._record_event(
            "master_command_response",
            {
                "station_id": station_config.station_id,
                "task_id": task_id,
                "ioa": 3001,
                "target_kw": target_kw,
                "positive_confirmation": positive,
                "stack_result": stack_result,
                "outcome": outcome,
                "connection_epoch": epoch,
                "dispatch_id": dispatch_id,
                "execution_reached": None,
            },
        )
        return CommandResult(
            task_id, station_config.station_id, outcome, positive, stack_result, epoch
        )

    def _publish_observation(
        self, station_id: str, ioa: int, value: float, valid: bool, cot: str
    ) -> None:
        # A concurrent allocator must not consume a point before its available_at
        # evidence has been published. Writer failure leaves the old cache intact.
        with self._observation_lock:
            self._record_event(
                "master_observed_point",
                {
                    "station_id": station_id,
                    "ioa": ioa,
                    "value": value if math.isfinite(value) else None,
                    "cot": cot,
                    "quality_valid": valid,
                },
            )
            key = (station_id, ioa)
            if valid:
                self._invalid_observations.discard(key)
            else:
                self._invalid_observations.add(key)
            self._observations[key] = (value, time.monotonic())

    def available_power(self, station_config: V01StationConfig) -> float:
        with self._observation_lock:
            sample = self._observations.get((station_config.station_id, 1008))
            invalid = (station_config.station_id, 1008) in self._invalid_observations
        if sample is None:
            raise ObservationUnavailable(station_config.station_id, "missing")
        if invalid or not math.isfinite(sample[0]) or sample[0] < 0:
            raise ObservationUnavailable(station_config.station_id, "invalid")
        age = time.monotonic() - sample[1]
        if age < 0 or age > station_config.telemetry_period_s * 2 + 1:
            raise ObservationUnavailable(station_config.station_id, "stale")
        return sample[0]

    def stop(self) -> None:
        try:
            if self._managed_lifecycle:
                self._quiesce_connections()
        finally:
            self.client.stop()
        self._record_event("master_stopped", {})

    def _wait_callback_states(self, stations: list[str], expected: str) -> None:
        with self._lifecycle_condition:
            if not self._lifecycle_condition.wait_for(
                lambda: all(self._callback_states.get(s) == expected for s in stations),
                timeout=3,
            ):
                raise TimeoutError(f"protocol lifecycle callbacks did not reach {expected}")

    def _quiesce_connections(self) -> None:
        """Drain ordered STOPDT and CLOSED callbacks before stopping c104."""
        self._record_event("master_quiescing", {})
        opened = []
        for station, connection in self._connections.items():
            if connection.state.name == "OPEN":
                if not connection.mute():
                    raise RuntimeError(f"STOPDT request failed: {station}")
                opened.append(station)
        self._wait_callback_states(opened, "OPEN_MUTED")
        for connection in self._connections.values():
            connection.disconnect()
        self._wait_callback_states(list(self._connections), "CLOSED")
        self._record_event("master_quiesced", {})
