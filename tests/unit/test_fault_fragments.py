"""Synthetic TCP/control dictionaries verify fault-fragment classification only."""

from copy import deepcopy
from typing import Any

import pytest

from pvcomm.qc.fault_fragments import account_control_gaps, resolve_control_gap
from pvcomm.qc.pcap import Stream


def sources() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stream = Stream(100)
    assert stream.feed(112, bytes.fromhex("680401004000"), 80, 30) == []
    connection = dict(
        src_ip="10.1.1.1", dst_ip="10.1.1.2", src_port=4000, dst_port=2404, connection_generation=1
    )
    # The captured stream is already contiguous up to sequence 106.
    stream.cursor = 6
    fragment = dict(**connection, **stream.fragment())
    p0 = [
        dict(
            **connection,
            tcp_start_seq=106,
            frame_type="S",
            data_hex="680401003200",
            available_at=15,
            packet_index=20,
            source_frame_refs=[20],
        ),
        dict(
            **connection,
            tcp_start_seq=112,
            frame_type="S",
            data_hex="680401004000",
            available_at=25,
            packet_index=21,
            source_frame_refs=[21],
        ),
    ]
    return fragment, p0


def test_pending_control_is_not_promoted_to_complete_apdu() -> None:
    fragment, p0 = sources()
    proof = resolve_control_gap(fragment, p0, 10, 20)
    assert proof["passed"]
    assert proof["p1_fragment"]["pending_bytes"] == 6
    assert proof["lost_p0_control"]["packet_index"] == 20
    assert proof["classification"] == "observed_transport_gap_not_complete_APDU"


@pytest.mark.parametrize(
    "field,value",
    [
        ("assembled_bytes", 1),
        ("gap_bytes", 7),
        ("pending_bytes", 5),
        ("contiguous", False),
        ("dst_port", 2405),
        ("connection_generation", 2),
        ("data_hex", "680443000000"),  # TESTFR is outside this S-only rule.
        ("data_hex", "680400000000"),  # I-frame bytes are never exempted.
        ("first_capture_utc_ns", 24),
    ],
)
def test_unexplained_or_non_control_fragments_still_fail(field: str, value: Any) -> None:
    fragment, p0 = sources()
    fragment[field] = value
    assert not resolve_control_gap(fragment, p0, 10, 20)["passed"]


def test_missing_or_ambiguous_primary_evidence_and_fault_boundary_fail() -> None:
    fragment, p0 = sources()
    assert not resolve_control_gap(fragment, p0[1:], 10, 20)["passed"]
    assert not resolve_control_gap(fragment, p0 + [p0[0]], 10, 20)["passed"]
    assert not resolve_control_gap(fragment, p0, 10, 15)["passed"]
    assert not resolve_control_gap(fragment, p0, 16, 20)["passed"]
    p0[0]["data_hex"] = "680443000000"
    assert not resolve_control_gap(fragment, p0, 10, 20)["passed"]


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    fragment, p0 = sources()
    experiment = dict(
        passed=False,
        qc_version="test",
        run_integrity=dict(passed=True),
        normal_regression=dict(passed=False),
        data_quality=dict(
            passed=False,
            checks={
                "independent_endpoint_capture_quality": dict(
                    passed=False,
                    evidence={
                        "PV01": dict(incomplete_bytes=6, terminal_callback_match=True),
                    },
                ),
                "zero_capture_drops": dict(passed=True),
            },
        ),
    )
    captures = dict(
        primary=dict(apdus=p0),
        diagnostic_rtu1=dict(
            incomplete_byte_count=6,
            incomplete_streams=[fragment],
        ),
    )
    return experiment, captures


def test_target_scope_callbacks_and_other_quality_gates_are_preserved() -> None:
    experiment, captures = fixture()
    original = deepcopy(experiment)
    proof = account_control_gaps(experiment, captures, [("PV01", "10.1.1.2")], ("PV01",), 10, 20)
    assert proof["passed"] and experiment["passed"]
    assert experiment["normal_regression"] == original["normal_regression"]
    retained = experiment["data_quality"]["checks"]["independent_endpoint_capture_quality"]
    assert (
        retained["strict_normal_comparison"]
        == original["data_quality"]["checks"]["independent_endpoint_capture_quality"]
    )
    for missing in ["target", "callback", "capture"]:
        experiment, captures = fixture()
        targets = () if missing == "target" else ("PV01",)
        if missing == "callback":
            experiment["data_quality"]["checks"]["independent_endpoint_capture_quality"][
                "evidence"
            ]["PV01"]["terminal_callback_match"] = False
        if missing == "capture":
            experiment["data_quality"]["checks"]["zero_capture_drops"]["passed"] = False
        account_control_gaps(experiment, captures, [("PV01", "10.1.1.2")], targets, 10, 20)
        assert not experiment["passed"]
