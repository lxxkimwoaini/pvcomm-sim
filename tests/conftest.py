from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_text() -> str:
    return (ROOT / "configs/foundation.yaml").read_text(encoding="utf-8")
