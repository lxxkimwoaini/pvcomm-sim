"""Analytic measurement summary tests; no invented experiment results."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pvcomm.qc import performance
from pvcomm.qc.performance import distribution, web_performance_report


def test_empty_metrics_remain_unknown_and_quantiles_are_nearest_rank() -> None:
    assert distribution([]) == dict(samples=0, mean=None, p95=None, p99=None, max=None)
    result = distribution(range(1, 101))
    assert result == dict(samples=100, mean=50.5, p95=95, p99=99, max=100)


@pytest.fixture
def analytic_web_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic analytic fixture only, never an experiment acceptance artifact."""
    monkeypatch.setattr(performance, "sys", SimpleNamespace(platform="linux"))
    (tmp_path / "workload.json").write_text(
        json.dumps(
            dict(
                origin_utc_ms=0,
                phase_seconds=1200,
                standalone=False,
                client_code_dirty=False,
                api_measurements_linux=str(tmp_path),
            )
        ),
        encoding="utf-8",
    )
    rows: dict[str, list[dict[str, Any]]] = {
        "phases": [dict(phase=p) for p in ("off", "live", "replay", "completed")],
        "requests": [
            dict(phase=p, elapsed_s=t, status=200, bytes=10, latency_ms=50, server_ms=20)
            for p, t in (("live", 1205), ("live", 2399), ("replay", 2401), ("replay", 3599))
        ],
        "display": [
            dict(mode=m, elapsed_s=t, newly_displayed_power=[dict(master_available_to_paint_ms=d)])
            for m, t, d in (("live", 1205, 1000), ("replay", 2405, 999999), ("live", 0, 999999))
        ],
        "resources": [
            dict(
                ts_utc_ns=t * 10**9,
                cpu_percent_one_core=10,
                rss_bytes=100,
                disk_read_bytes=0,
                disk_write_bytes=0,
            )
            for t in (1205, 2405)
        ],
        "windows-resources": [
            dict(ts_utc_ms=t * 1000, cpu_percent_one_core=20, working_set_bytes_sum=200)
            for t in (1205, 2405)
        ],
        "browser": [dict(elapsed_s=1205, metrics=[dict(name="JSHeapUsedSize", value=100)])],
    }
    for name, values in rows.items():
        (tmp_path / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in values), encoding="utf-8"
        )
    return tmp_path


def test_web_summary_excludes_replay_and_initial_ages(analytic_web_evidence: Path) -> None:
    report = web_performance_report(analytic_web_evidence)
    assert report["passed"]
    assert report["live_new_power_available_to_approximate_paint_ms"]["samples"] == 1
    assert report["live_new_power_available_to_approximate_paint_ms"]["p95"] == 1000


@pytest.mark.parametrize("problem", ["missing_live_samples", "error", "short_workload"])
def test_web_summary_rejects_incomplete_or_failed_evidence(
    analytic_web_evidence: Path, problem: str
) -> None:
    root = analytic_web_evidence
    if problem == "missing_live_samples":
        (root / "display.jsonl").write_text("", encoding="utf-8")
    elif problem == "error":
        (root / "errors.jsonl").write_text('{"fatal":true}\n', encoding="utf-8")
    else:
        path = root / "workload.json"
        workload = json.loads(path.read_text(encoding="utf-8"))
        workload["phase_seconds"] = 10
        path.write_text(json.dumps(workload), encoding="utf-8")
    assert not web_performance_report(root)["passed"]
