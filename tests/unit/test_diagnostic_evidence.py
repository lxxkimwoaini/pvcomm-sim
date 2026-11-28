"""Explicit synthetic evidence exercises access control, integrity and bounded reads."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pvcomm.config.workbench import ExperimentRequest
from pvcomm.pipeline.labels import file_sha256
from pvcomm.scenario.requests import submit_request
from pvcomm.web.app import create_app
from pvcomm.web.diagnostic_index import DiagnosticIndexes, RowOffsets
from pvcomm.web.experiments import ExperimentRepository


def fixture(root: Path) -> tuple[str, Path]:
    request = submit_request(root, ExperimentRequest(template="N09", seed=100))
    identity = request["experiment_id"]
    run = root / identity / "queue/task_1/attempt_1/run_fixture"
    source = run / "parsed/tshark-pdml-v2/iec104_objects.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            dict(
                run_id="run_fixture",
                apdu_id="primary-12-5",
                object_index=0,
                capture_id="primary",
                source_frame_refs=[11, 12],
                value_num=25,
                available_at=1795698741646842558,
            )
        )
        + "\n"
    )
    capture = run / "raw/pcap/primary.pcap"
    capture.parent.mkdir(parents=True)
    capture.write_bytes(b"explicit synthetic unit fixture; not a PCAP or experiment")
    (run / "manifest.json").write_text(
        json.dumps(
            dict(
                run_id="run_fixture",
                status="completed",
                cleanup_errors=[],
                code_version="fixture",
                captures=[dict(path="raw/pcap/primary.pcap", sha256=file_sha256(capture))],
            )
        )
    )
    labels = run / "labels/v04-label-v3/event_labels.jsonl"
    labels.parent.mkdir(parents=True)
    row = dict(
        run_id="run_fixture",
        station_id="PV01",
        evidence=[
            dict(
                path=source.relative_to(run).as_posix(),
                sha256=file_sha256(source),
                record_id="primary-12-5:0",
                tier="M1",
                role="online",
                available_at=1795698741646842558,
            )
        ],
    )
    labels.write_text(json.dumps(row) + "\n")
    return identity, run


def test_resolves_protocol_evidence_and_exact_pcap_frames_without_m2_leak(tmp_path: Path) -> None:
    identity, run = fixture(tmp_path / "requests")
    repository = ExperimentRepository(tmp_path / "requests")
    key = next(iter(repository.run_directories(identity)))
    client = TestClient(create_app(tmp_path / "m2", tmp_path / "requests"))
    base = f"/api/diagnostics/experiments/{identity}/runs/{key}"
    page = client.get(base + "/labels").json()
    link = page["evidence_links"][0]
    response = client.get(base + f"/labels/0/evidence/{link['evidence_key']}")
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["source_hash_verified"] is True
    assert evidence["record"]["available_at"] == "1795698741646842558"
    assert evidence["capture"]["frame_numbers"] == [11, 12]
    assert evidence["capture"]["wireshark_filter"] == "frame.number in {11 12}"
    assert client.get(base + "/labels/1/evidence/unknown").status_code == 404
    assert client.get(base + "/labels/-1/evidence/unknown").status_code == 422
    assert client.get("/api/runs").json() == {"runs": []}
    exported = client.get(base + "/export").json()
    item = next(f for f in exported["files"] if f["path"] == "raw/pcap/primary.pcap")
    response = client.get(base + "/export/" + item["file_key"])
    assert response.content == (run / item["path"]).read_bytes()
    assert response.headers["x-content-sha256"] == item["sha256"]
    assert client.get(base + "/export/unknown").status_code == 404


def test_reference_checksum_and_capture_checksum_cannot_be_bypassed(tmp_path: Path) -> None:
    identity, run = fixture(tmp_path)
    repository = ExperimentRepository(tmp_path)
    key = next(iter(repository.run_directories(identity)))
    link = repository.labels(identity, key, 0, 1).evidence_links[0]
    source = run / link["reference"]["path"]
    source.write_text(source.read_text().replace('"value_num": 25', '"value_num": 26'))
    with pytest.raises(ValueError, match="checksum"):
        repository.evidence(identity, key, 0, link["evidence_key"])
    inventory = repository.inventory(identity, key)
    item = next(f for f in inventory.files if f["path"] == "raw/pcap/primary.pcap")
    (run / item["path"]).write_bytes(b"tampered synthetic fixture")
    client = TestClient(create_app(tmp_path / "m2", tmp_path))
    assert (
        client.get(
            f"/api/diagnostics/experiments/{identity}/runs/{key}/export/{item['file_key']}"
        ).status_code
        == 409
    )


def test_pagination_and_record_lookup_reuse_offsets_and_close_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "".join(json.dumps(dict(run_id="r", event_id=str(i))) + "\n" for i in range(501))
    )
    cache = DiagnosticIndexes()
    calls = []
    original = RowOffsets.__init__

    def tracked(self: RowOffsets, *args: Any) -> None:
        calls.append(args[0])
        original(self, *args)

    monkeypatch.setattr(RowOffsets, "__init__", tracked)
    with cache.lock:
        index = cache.get(path, "r")
        assert index.page(500, 10) == [dict(run_id="r", event_id="500")]
        assert cache.get(path, "r").record("300")["event_id"] == "300"
        assert index.total == 501 and len(calls) == 1
        with pytest.raises(ValueError, match="cross-run"):
            cache.get(path, "another-run")
        with pytest.raises(FileNotFoundError):
            index.record("missing")
        path.write_text(path.read_text() + "{}\n")
        with pytest.raises(ValueError, match="changed"):
            cache.get(path, "r")
    temporary = Path(cache.temporary.name)
    cache.finalizer()
    assert not temporary.exists()


@pytest.mark.parametrize(
    "contents,match",
    [
        ('{"event_id":"a"}\n{"event_id":"a"}\n', "duplicate"),
        ('{"run_id":"different"}\n', "cross-run"),
        ('{"event_id":"a"}', "incomplete"),
    ],
)
def test_reject_invalid_sources_and_remove_partial_index(
    tmp_path: Path, contents: str, match: str
) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(contents)
    database = tmp_path / "index.sqlite"
    with pytest.raises(ValueError, match=match):
        RowOffsets(path, database, "r")
    assert not database.exists()


def test_export_refuses_running_or_symlinked_evidence(tmp_path: Path) -> None:
    identity, run = fixture(tmp_path)
    repository = ExperimentRepository(tmp_path)
    key = next(iter(repository.run_directories(identity)))
    path = run / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["status"] = "running"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="finalized"):
        repository.inventory(identity, key)
    manifest["status"] = "completed"
    path.write_text(json.dumps(manifest))
    outside = tmp_path / "private.txt"
    outside.write_text("explicit synthetic secret fixture")
    try:
        (run / "escape.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this account")
    with pytest.raises(ValueError, match="symbolic"):
        repository.inventory(identity, key)


def test_evidence_cannot_reference_a_path_outside_its_run(tmp_path: Path) -> None:
    identity, run = fixture(tmp_path)
    label_path = run / "labels/v04-label-v3/event_labels.jsonl"
    label = json.loads(label_path.read_bytes())
    label["evidence"][0]["path"] = "../secret.jsonl"
    label_path.write_text(json.dumps(label) + "\n")
    repository = ExperimentRepository(tmp_path)
    key = next(iter(repository.run_directories(identity)))
    link = repository.labels(identity, key, 0, 1).evidence_links[0]
    with pytest.raises(ValueError, match="unsafe"):
        repository.evidence(identity, key, 0, link["evidence_key"])


@pytest.mark.skipif(os.name != "nt", reason="Win32 long-path regression; Linux has no MAX_PATH")
def test_windows_deep_evidence_uses_extended_filesystem_namespace(tmp_path: Path) -> None:
    root = Path("\\\\?\\" + str(tmp_path.resolve())) / ("deep" * 22)
    identity, run = fixture(root)
    assert len(str(run / "parsed/tshark-pdml-v2/iec104_objects.jsonl")) > 260
    repository = ExperimentRepository(Path(str(root)[4:]))
    key = next(iter(repository.run_directories(identity)))
    link = repository.labels(identity, key, 0, 1).evidence_links[0]
    assert repository.evidence(identity, key, 0, link["evidence_key"]).source_hash_verified
