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


@pytest.mark.parametrize("command", ["label", "qc", "split", "features", "benchmark"])
def test_unimplemented_fails(command: str, tmp_path: Path) -> None:
    result = invoke(command, "--output-root", str(tmp_path / "outputs"))
    assert result.returncode == 3
    assert "not implemented" in result.stderr
    assert not (tmp_path / "outputs").exists()
