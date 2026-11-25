"""Synthetic kernel-readback fixtures; real NetEm requires the lab gate."""

import json
from typing import Any

import pytest

from pvcomm.config.multisite import NormalLink, V03Config
from pvcomm.config.presets import configuration
from pvcomm.network.regional import RegionalNetwork


def test_normal_profile_bounds_and_direction_seed_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        NormalLink(delay_ms=3, jitter_ms=4, seed=104)
    cfg = V03Config.model_validate(configuration(3, 120))
    network = RegionalNetwork(cfg)
    network._owned.append(network.master)
    link = cfg.regions[0].uplink
    assert link is not None
    network.shaped.append((network.master, "p0_1", link, True))
    row: dict[str, Any] = {
        "kind": "netem",
        "drops": 0,
        "options": {
            "delay": {"delay": link.delay_ms / 1000, "jitter": link.jitter_ms / 1000},
            "seed": link.seed + 1,
        },
    }
    monkeypatch.setattr(network, "_run", lambda *args: json.dumps([row]))
    assert network.shaping_evidence()[0]["reverse"]
    row["options"]["seed"] = link.seed
    with pytest.raises(RuntimeError, match="disagree"):
        network.shaping_evidence()
