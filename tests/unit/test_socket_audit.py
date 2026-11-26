"""Synthetic syscall text and dictionaries, solely verifier unit fixtures."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from pvcomm.capture.socket_audit import VERSION, SocketAudit
from pvcomm.qc.socket_audit import account_socket_quality, read_socket_calls, resolve_test_attempts


def fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extras = [
        {
            "event_id": "raw-1",
            "station_id": "PV01",
            "direction": "master_send",
            "data_hex": "680443000000",
            "ts_event_utc_ns": 12_010_000_000,
        }
    ]
    calls = [
        {
            "role": "master",
            "operation": "sendto",
            "fd": 8,
            "socket": "10.204.1.1:12345->10.204.1.2:2404",
            "data_hex": "680443000000",
            "size": 6,
            "returned": 6,
            "error": None,
            "ts_utc_ns": 12_000_000_000,
            "evidence_ref": "send:1",
        },
        {
            "role": "master",
            "operation": "shutdown",
            "fd": 8,
            "returned": -1,
            "error": "ENOTCONN",
            "ts_utc_ns": 13_000_000_000,
            "evidence_ref": "shutdown:2",
        },
        {
            "role": "master",
            "operation": "close",
            "fd": 8,
            "returned": 0,
            "error": None,
            "ts_utc_ns": 13_010_000_000,
            "evidence_ref": "close:3",
        },
    ]
    return extras, calls


def resolve(extras: list[dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, Any]:
    return resolve_test_attempts(
        extras, calls, {"PV01": "10.204.1.2"}, 10_000_000_000, 14_000_000_000
    )


def test_exact_test_attempt_requires_independent_disconnected_socket_evidence() -> None:
    extras, calls = fixture()
    result = resolve(extras, calls)
    assert result["passed"] and result["count"] == 1
    assert result["resolved"][0]["send"]["evidence_ref"] == "send:1"
    assert not resolve(extras, calls[:1])["passed"]
    assert not resolve(extras + extras, calls)["passed"]
    assert not resolve(extras, calls + [dict(calls[0], evidence_ref="another")])["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("direction", "master_receive"),
        ("station_id", "PV02"),
        ("data_hex", "680401000000"),
        ("ts_event_utc_ns", 14_000_000_000),
    ],
)
def test_other_frames_stations_and_half_open_boundary_never_resolved(
    field: str, value: Any
) -> None:
    extras, calls = fixture()
    extras[0][field] = value
    assert not resolve(extras, calls)["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "rtu1"),
        ("returned", -1),
        ("returned", 5),
        ("socket", "10.204.1.1:12345->10.204.2.2:2404"),
        ("ts_utc_ns", 11_000_000_000),
    ],
)
def test_failed_partial_wrong_socket_or_late_match_never_resolved(field: str, value: Any) -> None:
    extras, calls = fixture()
    calls[0][field] = value
    assert not resolve(extras, calls)["passed"]


def test_shutdown_must_prove_disconnection_before_actual_close() -> None:
    extras, calls = fixture()
    calls[1]["error"] = None
    assert not resolve(extras, calls)["passed"]
    calls[1]["error"] = "ENOTCONN"
    calls[1]["ts_utc_ns"] = 13_020_000_000
    assert not resolve(extras, calls)["passed"]


def manifest_fixture(root: Path) -> dict[str, Any]:
    directory = root / "truth/socket_audit"
    directory.mkdir(parents=True)
    text = (
        "12.000001 sendto(8<TCP:[10.204.1.1:12345->10.204.1.2:2404]>, "
        '"\\x68\\x04\\x43\\x00\\x00\\x00", 6, MSG_DONTWAIT|MSG_NOSIGNAL, NULL, 0) = 6\n'
        "13.000000 shutdown(8<TCP:[321]>, SHUT_RDWR) = -1 ENOTCONN "
        "(Transport endpoint is not connected)\n"
        "13.010000 close(8<TCP:[321]>) = 0\n"
        "13.100000 +++ exited with 0 +++\n"
    )
    path = directory / "master.100"
    path.write_text(text, encoding="utf-8")
    return {
        "network": {"rtus": []},
        "dependencies": {"c104": "2.2.1"},
        "socket_audit": {
            "version": VERSION,
            "scope": "Oracle",
            "completed": True,
            "workers": {"master": {"worker_pid": 100, "tracer_pid": 101}},
            "files": [
                {
                    "role": "master",
                    "path": "truth/socket_audit/master.100",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ],
        },
    }


def test_parser_checks_hash_paths_inventory_and_completion(tmp_path: Path) -> None:
    manifest = manifest_fixture(tmp_path)
    calls = read_socket_calls(tmp_path, manifest)
    assert len(calls) == 3 and calls[0]["ts_utc_ns"] == 12_000_001_000
    assert calls[0]["data_hex"] == "680443000000"
    manifest["socket_audit"]["files"][0]["sha256"] = "wrong"
    with pytest.raises(ValueError, match="hash"):
        read_socket_calls(tmp_path, manifest)
    manifest["socket_audit"]["files"][0]["path"] = "../outside.100"
    with pytest.raises(ValueError, match="outside"):
        read_socket_calls(tmp_path, manifest)


def test_unlisted_file_and_missing_main_worker_fail_closed(tmp_path: Path) -> None:
    manifest = manifest_fixture(tmp_path)
    (tmp_path / "truth/socket_audit/master.102").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        read_socket_calls(tmp_path, manifest)


@pytest.mark.parametrize(
    "missing,bad_quality,proof",
    [(0, False, True), (1, False, True), (0, True, True), (0, False, False)],
)
def test_accounting_keeps_normal_result_and_other_quality_failures(
    missing: int, bad_quality: bool, proof: bool
) -> None:
    strict = {"passed": False, "evidence": {"missing_callbacks": missing, "extra_callbacks": 1}}
    experiment: dict[str, Any] = {
        "qc_version": "synthetic_test",
        "normal_regression": {"passed": False},
        "run_integrity": {"passed": True},
        "data_quality": {
            "passed": False,
            "checks": {
                "business_callback_correspondence": strict,
                "log_capture_alignment": {"passed": not bad_quality},
            },
        },
    }
    account_socket_quality(experiment, {"passed": proof, "count": 1})
    assert experiment["normal_regression"]["passed"] is False
    assert experiment["passed"] is (missing == 0 and not bad_quality and proof)
    if experiment["passed"]:
        assert (
            experiment["data_quality"]["checks"]["business_callback_correspondence"][
                "strict_normal_comparison"
            ]
            is strict
        )


def test_command_is_fixed_and_preserves_worker_pid_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pvcomm.capture.socket_audit.shutil.which", lambda _: "/usr/bin/strace")
    monkeypatch.setattr(
        "pvcomm.capture.socket_audit.subprocess.check_output",
        lambda *a, **k: "strace -- version test\n",
    )
    audit = SocketAudit(tmp_path)
    command = audit.command("master", ["python", "-m", "pvcomm.scenario.lab_worker"])
    assert command[:3] == ["/usr/bin/strace", "-D", "-ff"]
    with pytest.raises(ValueError, match="owned"):
        audit.command("../../outside", ["python"])
