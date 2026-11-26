"""Classify an evidenced A01 TCP gap without inventing deliverable APDUs."""

from typing import Any


def s_control(hex_data: str) -> bool:
    raw = bytes.fromhex(hex_data)
    return len(raw) == 6 and raw[:4] == b"\x68\x04\x01\x00" and raw[4] & 1 == 0


def resolve_control_gap(
    fragment: dict[str, Any],
    p0: list[dict[str, Any]],
    start: int,
    end: int,
) -> dict[str, Any]:
    """Only one whole S frame waiting behind one S frame lost in the actual block.

    The pending bytes stay incomplete. No application bytes, unknown gaps,
    partial packets, unselected streams or unproven losses are exempted.
    """
    connection = ("src_ip", "dst_ip", "src_port", "dst_port", "connection_generation")
    same = [r for r in p0 if all(r[k] == fragment[k] for k in connection)]
    before = [r for r in same if r["tcp_start_seq"] == fragment["next_expected_tcp_seq"]]
    after = [r for r in same if r["tcp_start_seq"] == fragment.get("pending_tcp_seq")]
    supported = (
        fragment["assembled_bytes"] == 0
        and fragment["pending_bytes"] == fragment.get("gap_bytes") == 6
        and fragment.get("contiguous") is True
        and s_control(fragment.get("data_hex", ""))
        and len(before) == len(after) == 1
    )
    if supported:
        lost, pending = before[0], after[0]
        supported = (
            lost["frame_type"] == pending["frame_type"] == "S"
            and s_control(lost["data_hex"])
            and start <= lost["available_at"] < end
            and lost["available_at"] < pending["available_at"] <= fragment["first_capture_utc_ns"]
            and pending["data_hex"] == fragment["data_hex"]
            and (lost["tcp_start_seq"] + 6) % 2**32 == pending["tcp_start_seq"]
        )
    return dict(
        passed=bool(supported),
        p1_fragment=fragment,
        lost_p0_control=before[0] if len(before) == 1 else None,
        pending_p0_control=after[0] if len(after) == 1 else None,
        classification="observed_transport_gap_not_complete_APDU" if supported else "unresolved",
    )


def account_control_gaps(
    experiment: dict[str, Any],
    captures: dict[str, dict[str, Any]],
    station_addresses: list[tuple[str, str]],
    targets: tuple[str, ...],
    start: int,
    end: int,
) -> dict[str, Any]:
    quality = experiment["data_quality"]["checks"]
    strict = quality["independent_endpoint_capture_quality"]
    resolutions = {}
    valid = True
    for i, (station, address) in enumerate(station_addresses, 1):
        endpoint = strict["evidence"][station]
        capture = captures[f"diagnostic_rtu{i}"]
        fragments = capture["incomplete_streams"]
        if endpoint["incomplete_bytes"] == 0:
            valid &= not fragments
            continue
        own = [resolve_control_gap(f, captures["primary"]["apdus"], start, end) for f in fragments]
        # Only downlink TCP gaps on the explicitly affected station. Normal
        # endpoint callback accounting remains mandatory and unchanged.
        passed = (
            station in targets
            and endpoint["terminal_callback_match"]
            and sum(f["assembled_bytes"] + f["pending_bytes"] for f in fragments)
            == endpoint["incomplete_bytes"]
            == capture["incomplete_byte_count"]
            and bool(own)
            and all(r["passed"] for r in own)
            and all(f["dst_ip"] == address and f["dst_port"] == 2404 for f in fragments)
        )
        resolutions[station] = dict(passed=bool(passed), fragments=own)
        valid &= passed
    proof = dict(
        passed=bool(valid),
        stations=resolutions,
        scope="A01_selected_downlink_S_control_gap_only_never_reassembled_or_application",
        incomplete_bytes_still_retained=True,
    )
    if resolutions:
        quality["a01_control_gap_evidence"] = proof
        if valid and all(e["terminal_callback_match"] for e in strict["evidence"].values()):
            quality["independent_endpoint_capture_quality"] = dict(
                passed=True,
                strict_normal_comparison=strict,
                transport_fragment_resolution=proof,
            )
        experiment["qc_version"] += "+A01-control-gap.1"
        experiment["data_quality"]["passed"] = all(c["passed"] for c in quality.values())
        experiment["passed"] = (
            experiment["run_integrity"]["passed"] and experiment["data_quality"]["passed"]
        )
    return proof
