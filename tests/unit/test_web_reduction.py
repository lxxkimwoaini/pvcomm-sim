"""Synthetic display fixtures; scientific evidence is never downsampled here."""

from pvcomm.web.runs import curve_points


def test_reduction_preserves_extremes_endpoints_and_source_gaps() -> None:
    rows = [
        {
            "available_at": i,
            "sim_elapsed_ms": i * 2000 + (20000 if i >= 300 else 0),
            "value": 900.0 if i == 234 else -5.0 if i == 345 else 10.0,
        }
        for i in range(600)
    ]
    points = curve_points(rows, period_s=2, budget=120)
    assert len(points) <= 120
    assert points[0].available_at == 0 and points[-1].available_at == 599
    assert {900, -5}.issubset({p.value for p in points})
    assert sum(p.gap_before for p in points) == 1
    assert all(rows[p.available_at]["value"] == p.value for p in points)
