"""Analytic measurement summary tests; no invented experiment results."""

from pvcomm.qc.performance import distribution


def test_empty_metrics_remain_unknown_and_quantiles_are_nearest_rank() -> None:
    assert distribution([]) == dict(samples=0, mean=None, p95=None, p99=None, max=None)
    result = distribution(range(1, 101))
    assert result == dict(samples=100, mean=50.5, p95=95, p99=99, max=100)
