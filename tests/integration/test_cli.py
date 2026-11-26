import json
import subprocess
import sys
from pathlib import Path

import pytest


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pvcomm.cli", *args], capture_output=True, text=True
    )


def test_cli_validates_without_outputs(tmp_path: Path, config_text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(config_text, encoding="utf-8")
    result = invoke("validate-config", "--config", str(path))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True
    assert json.loads(result.stderr)["event"] == "config_valid"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]


def test_cli_error(tmp_path: Path) -> None:
    result = invoke("validate-config", "--config", str(tmp_path / "missing"))
    assert result.returncode == 2
    assert not result.stdout
    assert json.loads(result.stderr)["event"] == "config_invalid"


def test_run_and_parse_invalid_inputs_preserve_output_root(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    run = invoke("run", "--config", str(tmp_path / "missing"), "--output-root", str(root))
    assert run.returncode != 0
    assert not root.exists()
    parse = invoke("parse", "--run-dir", str(tmp_path / "missing"))
    assert parse.returncode != 0
    assert not root.exists()
    qc = invoke("qc", "--run-dir", str(tmp_path / "missing"))
    assert qc.returncode == 1
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("command", ["split", "features", "benchmark"])
def test_unimplemented_fails(command: str, tmp_path: Path) -> None:
    result = invoke(command, "--output-root", str(tmp_path / "outputs"))
    assert result.returncode == 3
    assert "not implemented" in result.stderr
    assert not (tmp_path / "outputs").exists()


def test_label_requires_actual_evidence(tmp_path: Path) -> None:
    result = invoke("label", "--run-dir", str(tmp_path / "missing"))
    assert result.returncode == 1
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize(
    "parameters",
    [
        ["--duration", "5"],
        ["--fault-type", "A02", "--delay-ms", "200", "--direction", "master_to_rtu"],
    ],
)
def test_fault_config_generator_and_cli_validation(tmp_path: Path, parameters: list[str]) -> None:
    output = tmp_path / "fault.json"
    command = [
        sys.executable,
        "scripts/make_fault_scenario.py",
        "--scope",
        "station",
        "--stations",
        "PV01",
        *parameters,
        "--output",
        str(output),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert invoke("validate-fault-scenario", "--config", str(output)).returncode == 0
    original = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == original


@pytest.mark.parametrize(
    "parameters",
    [
        ["--fault-type", "A02", "--delay-ms", "200"],
        [
            "--fault-type",
            "A02",
            "--duration",
            "5",
            "--delay-ms",
            "200",
            "--direction",
            "master_to_rtu",
        ],
        ["--duration", "5", "--delay-ms", "200"],
    ],
)
def test_fault_generator_rejects_mixed_or_incomplete_mechanisms(
    tmp_path: Path, parameters: list[str]
) -> None:
    output = tmp_path / "must_not_exist.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/make_fault_scenario.py",
            "--scope",
            "station",
            "--stations",
            "PV01",
            *parameters,
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode == 2 and not output.exists()
