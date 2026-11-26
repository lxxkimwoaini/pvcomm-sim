"""Synthetic traffic fixtures solely for unit tests, never accepted experiment data."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.scenes import canonical_hash
from pvcomm.config.similarity import SimilarityPolicy
from pvcomm.pipeline.iec104_parser import PARSER_VERSION
from pvcomm.pipeline.traffic import jsonl_records, summarize_traffic
from pvcomm.qc.similarity import (
    calibrate_thresholds,
    histogram_distance,
    match_traffic,
    traffic_distances,
)
from pvcomm.scenario.calibration import calibration_scenes


def packets_fixture(root: Path, alternate: bool = False) -> dict[str, Any]:
    root.mkdir()
    addresses = ["10.204.1.2", "10.204.1.3"]
    (root / "manifest.json").write_text(
        json.dumps(
            dict(
                run_id=root.name,
                status="completed",
                scene_clock=dict(start_mono_ns=0),
                clock_origin=dict(ts_utc_ns=0, ts_mono_ns=0),
                network=dict(addresses=addresses),
            )
        )
    )
    (root / "power_config.json").write_text(
        json.dumps(
            dict(
                protocol=dict(
                    duration_s=120, stations=[dict(station_id=f"PV{i + 1:02d}") for i in range(2)]
                )
            )
        )
    )
    directory = root / "parsed" / PARSER_VERSION
    directory.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    for index, at in enumerate((15.0, 15.02, 15.04, 20.0, 114.99, 115.0)):
        address = addresses[1 if alternate else 0]
        for direction in ("up", "down"):
            rows.append(
                dict(
                    run_id=root.name,
                    capture_id="primary",
                    packet_index=len(rows) + 1,
                    ts_capture_utc_ns=int(at * 1e9),
                    packet_len=64 + index,
                    src_ip=address if direction == "up" else "10.204.0.1",
                    dst_ip="10.204.0.1" if direction == "up" else address,
                )
            )
    (directory / "packets.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reversed(rows)))
    return summarize_traffic(root, 15, 115, ("PV01",), SimilarityPolicy())


def calibration_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(
            purpose="independent_normal_repeat_calibration",
            pair_id=f"unit_{i}",
            run_ids=[f"unit_{i}_a", f"unit_{i}_b"],
            passed=True,
            distances=traffic_distances(summary, summary),
            source_hashes={"fixture": "unit-only"},
        )
        for i in range(6)
    ]


def test_streaming_disk_sort_boundaries_and_burst_statistics(tmp_path: Path) -> None:
    result = packets_fixture(tmp_path / "normal")
    own = result["summaries"]["PV01"]["up"]
    assert own["scalars"]["packets"] == 5  # [15,115), excludes the end sample.
    assert own["scalars"]["bytes"] == 64 + 65 + 66 + 67 + 68
    assert own["scalars"]["burst_count"] == 1
    assert own["scalars"]["burst_packet_fraction"] == 3 / 5
    assert sum(own["distributions"]["iat_s"]["counts"]) == 4
    assert sum(own["distributions"]["packet_rate_s"]["counts"]) == 100
    assert result["summaries"]["global"] == result["summaries"]["PV01"]


def test_local_divergence_cannot_be_diluted_by_other_stations(tmp_path: Path) -> None:
    policy = SimilarityPolicy()
    normal = packets_fixture(tmp_path / "normal")
    changed = packets_fixture(tmp_path / "changed", alternate=True)
    calibration = calibrate_thresholds(policy, calibration_records(normal))
    result = match_traffic(normal, changed, policy, calibration)
    assert result["scopes"]["global"]["passed"]
    assert not result["scopes"]["PV01"]["passed"] and not result["matched"]
    assert result["nonmatch_is_invalid_experiment"] is False
    assert (tmp_path / "changed/manifest.json").is_file()


def test_calibration_precedes_selection_and_has_fixed_margin(tmp_path: Path) -> None:
    summary = packets_fixture(tmp_path / "fixture")
    records = calibration_records(summary)
    records[0]["distances"]["global"]["up.packets"] = 0.08
    calibration = calibrate_thresholds(SimilarityPolicy(), records)
    assert calibration.thresholds["global.up.packets"] == pytest.approx(0.1)
    assert calibration.thresholds["local.up.packets"] == 0.05
    with pytest.raises(ValueError, match="six independent"):
        calibrate_thresholds(SimilarityPolicy(), records[:5])
    duplicated = copy.deepcopy(records)
    duplicated[1]["run_ids"] = duplicated[0]["run_ids"]
    with pytest.raises(ValueError, match="distinct independent"):
        calibrate_thresholds(SimilarityPolicy(), duplicated)
    records[0]["purpose"] = "pair_evaluation"
    with pytest.raises(ValueError, match="normal repeats"):
        calibrate_thresholds(SimilarityPolicy(), records)


def test_frozen_policy_and_calibration_run_exclusion(tmp_path: Path) -> None:
    policy = SimilarityPolicy()
    summary = packets_fixture(tmp_path / "fixture")
    calibration = calibrate_thresholds(policy, calibration_records(summary))
    assert calibration.policy_sha256 == canonical_hash(policy.model_dump(mode="json"))
    reused = dict(summary, run_id=calibration.run_ids[0])
    with pytest.raises(ValueError, match="calibration runs cannot"):
        match_traffic(reused, summary, policy, calibration)
    other_policy = policy.model_copy(update=dict(length_bounds=(0, 256)))
    with pytest.raises(ValueError, match="another feature policy"):
        match_traffic(summary, summary, other_policy, calibration)


def test_distribution_distances_and_empty_traffic() -> None:
    a = dict(upper_bounds=[0, 64, None], counts=[0, 1, 0])
    b = dict(upper_bounds=[0, 64, None], counts=[0, 0, 1])
    zero = dict(upper_bounds=[0, 64, None], counts=[0, 0, 0])
    assert histogram_distance(a, a) == histogram_distance(zero, zero) == 0
    assert histogram_distance(a, b) == histogram_distance(a, zero) == 1


@pytest.mark.parametrize("mutation", ("cross_run", "P1", "incomplete"))
def test_bad_packet_sources_fail_closed(tmp_path: Path, mutation: str) -> None:
    root = tmp_path / "fixture"
    packets_fixture(root)
    path = root / "parsed" / PARSER_VERSION / "packets.jsonl"
    rows = list(jsonl_records(path))
    if mutation == "cross_run":
        rows[0]["run_id"] = "foreign"
    elif mutation == "P1":
        rows[0]["capture_id"] = "diagnostic_rtu1"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows).rstrip("\n")
        if mutation == "incomplete"
        else "".join(json.dumps(r) + "\n" for r in rows)
    )
    with pytest.raises(ValueError):
        summarize_traffic(root, 15, 115, ("PV01",), SimilarityPolicy())


def test_calibration_plan_is_reproducible_and_uses_separate_dates() -> None:
    first = calibration_scenes()
    assert first == calibration_scenes() and len(first) == 6
    assert len({scene.simulation.protocol.seed for _, scene in first}) == 6
    assert {scene.simulation.weather.source_start.day for _, scene in first} == {10, 11}
    assert {scene.normal_scene for _, scene in first} == {"N01", "N09", "N10"}
