"""Synthetic fixtures for V0.5.1 contracts; never research evidence."""

import json
from pathlib import Path
from typing import Any

import pytest

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.experiments.baselines import choose_threshold, evaluate
from pvcomm.pipeline.coverage import classify_event, event_hit
from pvcomm.pipeline.dataset import read_json, split_dataset, stratified_records, write_json
from pvcomm.pipeline.features import build_features
from pvcomm.qc.dataset import qc_dataset
from pvcomm.storage.parquet import source_hash


def policy() -> DatasetPolicy:
    return DatasetPolicy.model_validate_json(Path("configs/v0.5.1-dataset.json").read_bytes())


def event(start: int, end: int, **kwargs: Any) -> dict[str, Any]:
    return dict(
        run_id="unit",
        station_id="PV01",
        start_utc_ns=start * 10**9,
        end_utc_ns=end * 10**9,
        observable_at_utc_ns=start * 10**9,
        **kwargs,
    )


def metric(
    times: list[int], events: list[dict[str, Any]], predictions: list[int]
) -> dict[str, Any]:
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    indices = [dict(run_id="unit", station_id="PV01", window_end_utc_ns=t * 10**9) for t in times]
    labels = np.array(
        [
            int(any(e["start_utc_ns"] <= t * 10**9 - 1 < e["end_utc_ns"] for e in events))
            for t in times
        ]
    )
    return evaluate(labels, np.array(predictions), 0.5, indices, events, policy())


def test_registered_scales_and_legacy_are_distinct() -> None:
    assert [
        (policy().for_scale(s).window_s, policy().for_scale(s).step_s)
        for s in ("W10", "W30", "W60")
    ] == [(10, 1), (30, 1), (60, 5)]
    with pytest.raises(ValueError):
        DatasetPolicy(window_s=10)
    with pytest.raises(ValueError):
        policy().for_scale("W120")


def test_event_sampling_gaps_history_and_half_open_boundary() -> None:
    assert not event_hit(event(60, 65), 60 * 10**9)
    assert event_hit(event(60, 65), 65 * 10**9)
    assert classify_event(event(60, 65), [60 * 10**9, 70 * 10**9]) == "no_sample_endpoint"
    assert classify_event(event(1, 2), [60 * 10**9]) == "insufficient_history"
    result = metric([60, 70], [event(1, 2), event(61, 62), event(69, 72)], [0, 1])
    assert result["raw_event_count"] == 3 and result["event_count"] == 1
    assert result["detected_events"] == 1
    assert result["event_outcomes"][1]["evaluation_status"] == "no_sample_endpoint"


def test_violation_online_start_and_delay_components() -> None:
    e = event(50, 80)
    e["observable_at_utc_ns"] = 62 * 10**9
    result = metric([60, 65, 70], [e], [0, 0, 1])
    out = result["event_outcomes"][0]
    assert out["delay_s"] == 8 and out["delay_from_violation_s"] == 20
    assert out["history_wait_s"] == 0 and out["sampling_wait_s"] == 3
    assert out["detection_after_first_endpoint_s"] == 5


def test_continuous_alert_cannot_reuse_and_miss_is_not_censor() -> None:
    result = metric([60, 65, 70, 75], [event(59, 64), event(69, 74)], [1, 1, 1, 1])
    assert result["detected_events"] == 1 and result["missed_events"] == 1
    assert not any(e["censored"] for e in result["event_outcomes"])
    open_result = metric([60, 65], [event(59, 100, run_end_censored=True)], [0, 0])
    assert open_result["missed_events"] == open_result["run_end_censored_events"] == 1


def test_duplicate_alerts_and_normal_exposure_exclude_sampling_gap() -> None:
    result = metric([60, 65, 90, 95, 200], [event(59, 100)], [1, 0, 1, 0, 1])
    assert result["duplicate_alert_episodes"] == 1
    assert result["false_alert_episodes"] == 1
    assert result["normal_endpoint_exposure_station_hours"] == pytest.approx(5 / 3600)


def test_single_class_threshold_handles_tied_scores_without_changing_comparator() -> None:
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    y, scores = np.zeros(100), np.zeros(100)
    threshold = choose_threshold(y, scores)
    assert threshold > 0 and not np.any(scores >= threshold)
    assert choose_threshold(y, scores, legacy=True) == 0
    with pytest.raises(ValueError, match="normal validation"):
        choose_threshold(np.ones(10), np.zeros(10))
    assert choose_threshold(np.array([0, 0, 1, 1]), np.array([0.0, 0.0, 0.5, 0.5])) == 0.5


def jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def synthetic_catalogue(root: Path) -> None:
    """Six source-independent synthetic runs, all excluded from formal inventory."""
    rows = []
    for j in range(6):
        run = root / f"unit{j}"
        run.mkdir()
        stations = [
            dict(station_id=f"PV{i:02d}", telemetry_period_s=2, capacity_kw=30.0)
            for i in range(1, 4)
        ]
        write_json(run / "power_config.json", dict(protocol=dict(stations=stations, warmup_s=0)))
        write_json(run / "manifest.json", dict(network=dict(addresses=["a", "b", "c"])))
        jsonl(
            run / "observed/master_events.jsonl",
            [
                dict(
                    run_id=f"unit{j}",
                    event_type="master_business_finished",
                    available_at=120 * 10**9,
                    ts_event_utc_ns=120 * 10**9,
                )
            ],
        )
        packet = dict(
            run_id=f"unit{j}",
            capture_id="primary",
            packet_index=1,
            ts_capture_utc_ns=74 * 10**9,
            src_ip="a",
            dst_ip="master",
            packet_len=100,
            tcp_payload_len=20,
            tcp_retransmission=False,
        )
        apdu = dict(
            packet,
            available_at=74 * 10**9,
            source_frame_refs=[1],
            frame_type="I",
            type_id=13,
            cot=3,
            negative_confirm=False,
            apdu_id="unit_apdu",
        )
        for name, records in (
            ("packets", [packet]),
            ("iec104_apdu", [apdu]),
            ("iec104_objects", [dict(apdu, quality_raw=0)]),
        ):
            jsonl(run / f"parsed/tshark-pdml-v2/{name}.jsonl", records)
        labels = []
        for s in stations:
            fs = (
                [
                    dict(
                        finding_id=f"unit{j}_finding",
                        kind="reception_freshness",
                        station_id=s["station_id"],
                        outcome="synthetic_fixture",
                        service_violation=True,
                        label_status="valid",
                        start_utc_ns=70 * 10**9,
                        end_utc_ns=80 * 10**9,
                        observable_at_utc_ns=70 * 10**9,
                        recovery_utc_ns=80 * 10**9,
                        recovery_censored=False,
                        evidence=[],
                    )
                ]
                if s["station_id"] == "PV01"
                else []
            )
            labels.append(
                dict(
                    run_id=f"unit{j}",
                    anomaly_id=f"unit{j}_anomaly",
                    station_id=s["station_id"],
                    region_id="unit",
                    scope="station",
                    fault_applied=True,
                    fault_type=["A04"],
                    fault_start_utc_ns=60 * 10**9,
                    fault_end_utc_ns=80 * 10**9,
                    transport_impact=True,
                    service_violation=bool(fs),
                    observable_anomaly=bool(fs),
                    operating_context=["synthetic_fixture"],
                    label_status="valid",
                    findings=fs,
                    evidence=[],
                )
            )
        path = run / "labels/v04-label-v3/event_labels.jsonl"
        jsonl(path, labels)
        rows.append(
            dict(
                run_id=f"unit{j}",
                path=str(run),
                eligible=True,
                site_count=3,
                group_keys=[f"source:unit{j}"],
                duration_s=120,
                scene_clock=dict(start_utc_ns=0),
                label_path=str(path),
                evidence_sha256={},
                fault_types=["A04"],
                normal_scene="N01",
                violation_stations=1,
                weather=dict(source_start=f"2024-06-{j + 1:02d}T00:00:00Z"),
            )
        )
    jsonl(root / "runs.jsonl", rows)
    write_json(root / "inventory.json", dict(run_index_sha256=source_hash(root / "runs.jsonl")))


def test_multiscale_export_independent_qc_and_shared_stratification(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    synthetic_catalogue(tmp_path)
    report = split_dataset(tmp_path, policy())
    assert report["independent_groups"] == 6
    frozen = source_hash(tmp_path / "split/assignment.json")
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    profile = read_json(tmp_path / "split/source_profile.json")
    a, _ = stratified_records(rows, profile, policy().split_seed)
    b, _ = stratified_records(list(reversed(rows)), profile, policy().split_seed)
    assert a == b == read_json(tmp_path / "split/assignment.json")["runs"]
    for scale in ("W10", "W30", "W60"):
        built = build_features(tmp_path, scale=scale)
        checked = qc_dataset(tmp_path, scale=scale)
        assert built["positive_at_end"] > 0 and checked["passed"]
        assert source_hash(tmp_path / "split/assignment.json") == frozen
        coverage = json.loads((tmp_path / scale / "event_coverage.json").read_bytes())
        assert all(p["evaluable_event_runs"] > 0 for p in coverage["by_partition"].values())
        with pytest.raises(FileExistsError):
            build_features(tmp_path, scale=scale)


def test_independent_qc_rejects_feature_corruption_even_with_updated_table_hash(
    tmp_path: Path,
) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    pa = pytest.importorskip("pyarrow")
    synthetic_catalogue(tmp_path)
    split_dataset(tmp_path, policy())
    build_features(tmp_path, scale="W10")
    folder = tmp_path / "W10/features-M2"
    table = pq.read_table(folder / "features.parquet")
    values = table["packet_rate"].to_pylist()
    values[0] = 999.0
    table = table.set_column(
        table.schema.get_field_index("packet_rate"), "packet_rate", pa.array(values)
    )
    pq.write_table(table, folder / "features.parquet")
    quality = json.loads((folder / "quality.json").read_bytes())
    quality["tables"]["features"] = source_hash(folder / "features.parquet")
    (folder / "quality.json").write_text(json.dumps(quality), encoding="utf-8")
    checked = qc_dataset(tmp_path, scale="W10")
    assert not checked["passed"]
    assert not checked["checks"]["fixed_sample_independent_feature_recalculation"]


def test_reassembly_availability_negative_and_missing_cursor(tmp_path: Path) -> None:
    from pvcomm.qc.feature_evidence import reference_traffic

    packet = dict(
        ts_capture_utc_ns=1,
        src_ip="a",
        dst_ip="master",
        packet_len=100,
        tcp_payload_len=20,
        tcp_retransmission=False,
    )
    apdu = dict(packet, available_at=20, frame_type="I", type_id=13, cot=3, negative_confirm=False)
    before = reference_traffic([packet], [apdu], [], "a", 0, 20)
    after = reference_traffic([packet], [apdu], [], "a", 0, 21)
    assert before["up_packets"] == 1 and before["i_frames"] == 0
    assert after["i_frames"] == 1 and after["packet_rate"] == pytest.approx(1e9 / 21)
    assert classify_event(event(1, 2), [], 3 * 10**9) == "insufficient_history"


def test_reassembly_evidence_cannot_claim_early_availability(tmp_path: Path) -> None:
    from pvcomm.qc.feature_evidence import check_feature_evidence

    pq = pytest.importorskip("pyarrow.parquet")
    synthetic_catalogue(tmp_path)
    split_dataset(tmp_path, policy())
    build_features(tmp_path, scale="W10")
    run = tmp_path / "unit0"
    path = run / "parsed/tshark-pdml-v2/iec104_apdu.jsonl"
    ad = json.loads(path.read_text())
    ad["available_at"] = ad["ts_capture_utc_ns"] - 1
    jsonl(path, [ad])
    catalogue = {
        r["run_id"]: r
        for r in [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    }
    folder = tmp_path / "W10/features-M2"
    result = check_feature_evidence(
        catalogue,
        pq.read_table(folder / "index.parquet").to_pylist(),
        pq.read_table(folder / "features.parquet").to_pylist(),
        [],
    )
    assert not result["passed"]
    assert any(r["field"] == "reassembly_available_at" for r in result["failures"])
