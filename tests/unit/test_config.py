import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pvcomm.config import ConfigError, config_sha256, load_config, validate_yaml
from pvcomm.config.service import MAX_CONFIG_BYTES, canonical_json


def test_valid_config_is_frozen_and_canonical(config_text: str) -> None:
    config = validate_yaml(config_text)
    assert len(config.stations) == 3
    assert config.stations[1].capacity_kw == 100
    assert config_sha256(config) == config_sha256(validate_yaml(config_text + "\n# comment\n"))
    assert json.loads(canonical_json(config))["seed"] == 20261125
    with pytest.raises(ValidationError):
        config.seed = 0


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("schema_version: '0.0'", "schema_version: '1.0'"),
        ("seed: 20261125", "seed: true"),
        ("seed: 20261125", "seed: -1"),
        ("speed: 1.0", "speed: 2.0"),
        ("warmup_s: 120", "warmup_s: 1800"),
        ("duration_s: 1800", "duration_s: '1800'"),
        ("capacity_kw: 100", "capacity_kw: -1"),
        ("capacity_kw: 100", "capacity_kw: true"),
        ("capacity_kw: 100", "capacity_kw: '100'"),
        ("speed: 1.0", "speed: true"),
        ("capacity_kw: 100", "capacity_kw: .nan"),
        ("rated_kva: 110", "rated_kva: 90"),
        ("station_id: PV02", "station_id: PV01"),
        ("common_address: 2", "common_address: 1"),
        ("common_address: 2", "common_address: 65535"),
        ("device_profile: prototype_5s", "device_profile: missing"),
        ("region_id: region_A", "region_id: missing"),
        ("feeder_id: feeder_A", "feeder_id: missing"),
        ("run_id: foundation_001", "run_id: ../unsafe"),
        ("telemetry_period_s: 2", "telemetry_period_s: 3"),
    ],
)
def test_invalid_contract(config_text: str, old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        validate_yaml(config_text.replace(old, new))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "[unterminated",
        "!!python/object:os.system {}",
        "x: 1\nx: 2",
        "a: &a [*a]",
        "a: &a {x: 1}\nb: {<<: [*a, *a]}",
        "\ud800",
        "? [one, two]\n: value",
        "a" * (MAX_CONFIG_BYTES + 1),
    ],
)
def test_bad_yaml(text: str) -> None:
    with pytest.raises(ConfigError):
        validate_yaml(text)


def test_unknown_fields(config_text: str) -> None:
    with pytest.raises(ConfigError):
        validate_yaml(config_text + "lab_fault: {}\n")
    with pytest.raises(ConfigError):
        validate_yaml(config_text.replace("clock:", "clock:\n  arbitrary: 1"))


def test_error_identifies_field_without_echoing_input(config_text: str) -> None:
    with pytest.raises(ConfigError) as caught:
        validate_yaml(config_text.replace("seed: 20261125", "seed: invalid_value_marker"))
    message = str(caught.value)
    assert "seed:" in message
    assert "invalid_value_marker" not in message
    assert "errors.pydantic.dev" not in message


def test_read_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_bytes(b"a" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigError):
        load_config(bad)
