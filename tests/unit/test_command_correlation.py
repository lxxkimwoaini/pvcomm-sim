"""Synthetic event fixtures only; no manufactured simulation evidence."""

from typing import Any

import pytest

from pvcomm.qc.commands import audit_commands


def fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    master: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    for index, base in enumerate((0, 5_000_000_000)):
        task = str(index)
        for kind, offset, extra in (
            ("master_command_sent", 0, {"deadline_ms": 3000}),
            ("master_command_response", 4, {"positive_confirmation": True}),
            ("master_observed_point", 3, {"ioa": 1006, "value": 20.0}),
            ("master_observed_point", 5, {"ioa": 1001, "value": 20.0}),
        ):
            master.append(
                dict(
                    station_id="PV01",
                    event_id=f"{task}-{kind}-{offset}",
                    event_type=kind,
                    ts_mono_ns=base + offset,
                    ts_event_utc_ns=base + offset,
                    task_id=task,
                    target_kw=20.0,
                    **({"ioa": 3001} | extra),
                )
            )
        terminal.append(
            dict(
                station_id="PV01",
                event_id=f"a{task}",
                event_type="terminal_command_accepted",
                ts_mono_ns=base + 2,
                ts_event_utc_ns=base + 2,
                target_kw=20.0,
                ioa=3001,
                common_address=1,
            )
        )
        for cot, offset in ((6, 1), (7, 3)):
            objects.append(
                dict(
                    apdu_id=f"w{task}-{cot}",
                    ca=1,
                    ioa=3001,
                    type_id=50,
                    cot=cot,
                    src_ip="rtu" if cot == 7 else "master",
                    dst_ip="rtu" if cot == 6 else "master",
                    flow_id=index,
                    value_num=20.0,
                    available_at=base + offset,
                )
            )
    return master, terminal, objects


def audit(
    data: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return audit_commands(*data, "PV01", 1, "rtu")


def test_repeated_values_have_distinct_evidence() -> None:
    rows = audit(fixture())
    assert all(r["passed"] for r in rows)
    for key in (
        "accept_event",
        "response_event",
        "wire_apdu",
        "wire_ack_apdu",
        "setpoint_feedback_event",
        "power_feedback_event",
    ):
        assert len({r[key] for r in rows}) == 2


@pytest.mark.parametrize("kind", ["accept", "wire", "ack", "response", "setpoint", "power"])
def test_later_same_value_cannot_complete_missing_first_task(kind: str) -> None:
    master, terminal, wire = fixture()
    if kind == "accept":
        terminal.pop(0)
    elif kind in ("wire", "ack"):
        wire.pop(0 if kind == "wire" else 1)
    else:
        master.pop({"response": 1, "setpoint": 2, "power": 3}[kind])
    rows = audit((master, terminal, wire))
    assert not rows[0]["passed"]
    assert rows[1]["passed"]


@pytest.mark.parametrize("field", ["task_id", "station_id", "ioa", "target_kw"])
def test_response_must_belong_to_this_task(field: str) -> None:
    data = fixture()
    data[0][1][field] = {"task_id": "1", "station_id": "PV02", "ioa": 3002, "target_kw": 21.0}[
        field
    ]
    assert not audit(data)[0]["passed"]


@pytest.mark.parametrize("timestamp", [1, 5_000_000_000, 10_000_000_000])
def test_power_before_accept_or_at_exclusive_boundary_is_rejected(timestamp: int) -> None:
    data = fixture()
    data[0][3]["ts_mono_ns"] = timestamp
    assert not audit(data)[0]["passed"]


def test_exact_ack_deadline_duplicate_task_and_ambiguous_accept_fail() -> None:
    data = fixture()
    data[0][1]["ts_mono_ns"] = 3_000_000_000
    assert not audit(data)[0]["passed"]
    data = fixture()
    data[0][4]["task_id"] = "0"
    assert not any(r["passed"] for r in audit(data))
    data = fixture()
    data[1].append({**data[1][0], "event_id": "ambiguous"})
    assert not audit(data)[0]["passed"]


def test_ack_on_different_tcp_session_is_rejected() -> None:
    data = fixture()
    data[2][1]["flow_id"] = 999
    assert not audit(data)[0]["passed"]


def test_physical_shortage_uses_reachable_power_only_in_oracle_audit() -> None:
    data = fixture()
    for row in data[0]:
        if row["event_type"] == "master_observed_point" and row["ioa"] == 1001:
            row["value"] = 10.0
    assert not any(r["passed"] for r in audit(data))
    assert all(
        r["passed"]
        for r in audit_commands(
            *data, "PV01", 1, "rtu", reachable_power=lambda target, row: min(target, 10)
        )
    )


def test_repeated_action_uses_new_application_task_id() -> None:
    import threading
    from pathlib import Path
    from types import ModuleType, SimpleNamespace

    from pvcomm.config.lab import load_v01_config
    from pvcomm.protocol.c104_runtime import C104Master

    # Test double only for the local application ID/serialization contract.
    point = SimpleNamespace(value=10.0, transmit=lambda **_: True)
    endpoint = C104Master.__new__(C104Master)
    endpoint._command_lock = threading.Lock()
    endpoint._command_sequence = 0
    endpoint._c104 = ModuleType("synthetic_unit_c104")
    endpoint._c104.__dict__["Cot"] = SimpleNamespace(ACTIVATION=6)
    config = load_v01_config(Path("configs/v0.1-lab-smoke.yaml")).stations[0]
    endpoint._remote_stations = {config.station_id: SimpleNamespace(get_point=lambda **_: point)}
    records: list[dict[str, Any]] = []
    endpoint._record_event = lambda kind, data: records.append({"kind": kind, **data})
    endpoint.send_setpoint(config, 10, "limit")
    endpoint.send_setpoint(config, 10, "limit")
    sends = [r for r in records if r["kind"] == "master_command_sent"]
    responses = [r for r in records if r["kind"] == "master_command_response"]
    assert sends[0]["task_id"] != sends[1]["task_id"]
    assert [r["task_id"] for r in sends] == [r["task_id"] for r in responses]
