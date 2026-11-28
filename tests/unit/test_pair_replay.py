"""Synthetic pair records test causal projections; never claimed as real runs."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pvcomm.config.pairs import development_pair
from pvcomm.pipeline.labels import file_sha256
from pvcomm.web.app import create_app
from pvcomm.web.pair_replay import PairReplayRepository

START = 1_790_000_000_000_000_000


def fixture(root: Path) -> tuple[PairReplayRepository, str, Path]:
    pair = root / "task/attempt_1/pair_fixture"
    pair.mkdir(parents=True)
    config = development_pair("P06", "pair_fixture")
    (pair / "pair.json").write_text(config.model_dump_json())
    members = []
    for member, shift in (("normal", 0), ("changed", 1000_000_000_000)):
        identity = "run_" + member
        directory = pair / identity
        (directory / "observed").mkdir(parents=True)
        start = START + shift
        (directory / "manifest.json").write_text(
            json.dumps(
                dict(run_id=identity, status="completed", scene_clock=dict(start_utc_ns=start))
            )
        )
        rows = [
            dict(
                run_id=identity,
                event_id=f"fixture-{i}",
                event_type="master_observed_point",
                station_id="PV01",
                ioa=1001,
                value=value,
                quality_valid=valid,
                available_at=start + seconds * 1_000_000_000,
                sim_elapsed_ms=seconds * 1000,
            )
            for i, (seconds, value, valid) in enumerate(
                ((1, 10, True), (2, 20, True), (3, 30, True), (4, 40, False), (5, 50, True))
            )
        ]
        (directory / "observed/master_events_master.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
        # Deliberately conflicting Oracle value must never be used to repair observations.
        (directory / "truth").mkdir()
        (directory / "truth/electrical_state.jsonl").write_text('{"actual_kw": 999}\n')
        members.append(dict(member=member, run_id=identity, run_dir=identity, status="completed"))
    (pair / "pair-manifest.json").write_text(
        json.dumps(dict(pair_id="pair_fixture", status="completed", members=members))
    )
    report_path = pair / "qc/v04-traffic-similarity-v1/report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            dict(
                pair_id="pair_fixture",
                matched=False,
                hard_pair_selected=False,
                summaries={
                    member: dict(run_id="run_" + member, window_s=[0, 10])
                    for member in ("normal", "changed")
                },
                source_hashes={"pair.json": file_sha256(pair / "pair.json")},
            )
        )
    )
    repository = PairReplayRepository(None, root)
    key = repository.list(0, 20).pairs[0].pair_key
    return repository, key, pair


def test_each_member_uses_own_causal_clock_and_never_oracle(tmp_path: Path) -> None:
    repository, key, _pair = fixture(tmp_path)
    result = repository.replay(key, "PV01", 1001, 3)
    assert len(result.members) == 2
    for member in result.members:
        assert [p.value for p in member.points] == [10, 20]
        assert all(p.available_at < member.cursor_utc_ns_exclusive for p in member.points)
        assert member.latest_value == 20
        assert member.source == "master_iec104_telemetry"
    assert (
        result.members[1].cursor_utc_ns_exclusive - result.members[0].cursor_utc_ns_exclusive
        == 1000_000_000_000
    )
    assert result.traffic_report is None
    assert result.traffic_unavailable_reason is not None
    assert repository.replay(key, "PV01", 1001, 10).traffic_report is not None
    # Repeated replay reuses the two immutable file indexes, no full JSONL rescans.
    indexes = dict(repository.indexes.indexes)
    repository.replay(key, "PV01", 1001, 3)
    assert all(repository.indexes.indexes[path] is index for path, index in indexes.items())


def test_invalid_missing_and_stale_are_unknown_not_zero(tmp_path: Path) -> None:
    repository, key, _pair = fixture(tmp_path)
    assert repository.replay(key, "PV01", 1001, 1).members[0].points == []
    assert repository.replay(key, "PV01", 1001, 5).members[0].latest_value is None
    late = repository.replay(key, "PV01", 1001, 20)
    assert all(m.latest_value is None for m in late.members)
    assert all(999 not in [p.value for p in m.points] for m in late.members)
    assert all(m.points[-1].gap_before for m in late.members)
    assert repository.replay(key, "PV02", 1001, 10).members[0].points == []


@pytest.mark.parametrize("change", ["member_path", "duplicate", "cross_run", "running", "source"])
def test_tampered_or_live_evidence_is_rejected(tmp_path: Path, change: str) -> None:
    repository, key, pair = fixture(tmp_path)
    repository.replay(key, "PV01", 1001, 3)
    path = pair / "pair-manifest.json"
    record = json.loads(path.read_bytes())
    if change == "member_path":
        record["members"][0]["run_dir"] = "../outside"
    elif change == "duplicate":
        record["members"][1] = record["members"][0]
    elif change == "running":
        record["status"] = "running"
    elif change == "cross_run":
        manifest = pair / "run_normal/manifest.json"
        row = json.loads(manifest.read_bytes())
        row["run_id"] = "wrong_run"
        manifest.write_text(json.dumps(row))
    else:
        source = pair / "run_normal/observed/master_events_master.jsonl"
        source.write_text(source.read_text() + "{}\n")
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        repository.replay(key, "PV01", 1001, 3)


def test_report_hash_and_member_identity_are_verified(tmp_path: Path) -> None:
    repository, key, pair = fixture(tmp_path)
    repository.replay(key, "PV01", 1001, 10)
    # A new valid JSON config with different bytes must fail the frozen report hash.
    source = pair / "pair.json"
    source.write_text(source.read_text() + "\n")
    with pytest.raises(ValueError, match="checksum"):
        repository.replay(key, "PV01", 1001, 10)


def test_archive_api_is_read_only_and_separate_from_m2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repository, key, pair = fixture(tmp_path)
    monkeypatch.setenv("PVCOMM_PAIR_REPLAY_ROOT", str(tmp_path))
    client = TestClient(create_app(tmp_path / "empty_m2"))
    assert client.get("/api/diagnostics/pairs").json()["total"] == 1
    assert client.get("/api/diagnostics/pairs?limit=100").status_code == 422
    endpoint = f"/api/diagnostics/pairs/{key}/replay"
    response = client.get(endpoint + "?at_s=3")
    assert response.status_code == 200
    assert response.json()["scope"] == "Oracle_experiment_management_never_M2_features"
    assert isinstance(response.json()["members"][0]["cursor_utc_ns_exclusive"], str)
    for query in ("?station_id=PV99", "?ioa=999", "?at_s=999999"):
        assert client.get(endpoint + query).status_code == 409
    assert client.post(endpoint).status_code == 405
    assert client.get("/api/runs").json() == {"runs": []}
    assert client.get(f"/api/runs/{key}/snapshot").status_code == 404
    before = file_sha256(pair / "pair-manifest.json")
    client.get(endpoint)
    assert before == file_sha256(pair / "pair-manifest.json")


def test_long_replay_is_bounded_and_cursor_excludes_equal_timestamp(tmp_path: Path) -> None:
    repository, key, pair = fixture(tmp_path)
    for member, shift in (("normal", 0), ("changed", 1000_000_000_000)):
        source = pair / f"run_{member}/observed/master_events_master.jsonl"
        source.write_text(
            "".join(
                json.dumps(
                    dict(
                        run_id=f"run_{member}",
                        event_id=f"bounded-{i}",
                        event_type="master_observed_point",
                        station_id="PV01",
                        ioa=1001,
                        value=float(i),
                        available_at=START + shift + i * 1_000_000,
                        sim_elapsed_ms=i,
                    )
                )
                + "\n"
                for i in range(10001)
            )
        )
    result = repository.replay(key, "PV01", 1001, 10)
    for curve in result.members:
        assert len(curve.points) <= 240
        assert curve.points[0].sim_elapsed_ms == 9400
        assert curve.points[-1].sim_elapsed_ms == 9999
        assert curve.latest_value == 9999
        assert all(p.available_at < curve.cursor_utc_ns_exclusive for p in curve.points)
