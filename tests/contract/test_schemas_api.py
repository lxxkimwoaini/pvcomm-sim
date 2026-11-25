import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from pvcomm.config import FoundationConfig, config_sha256, validate_yaml
from pvcomm.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contracts() -> None:
    schema = json.loads((ROOT / "schemas/foundation.schema.json").read_text(encoding="utf-8"))
    assert schema == FoundationConfig.model_json_schema()
    Draft202012Validator.check_schema(schema)
    assert (
        json.loads((ROOT / "schemas/openapi.json").read_text(encoding="utf-8"))
        == create_app().openapi()
    )


def test_schema_accepts_example(config_text: str) -> None:
    config = validate_yaml(config_text)
    Draft202012Validator(FoundationConfig.model_json_schema()).validate(
        config.model_dump(mode="json")
    )


def test_api_matches_cli_service(config_text: str) -> None:
    client = TestClient(create_app())
    assert client.get("/api/health").json() == {
        "status": "ok",
        "stage": "V0.0",
        "simulation_available": False,
    }
    result = client.post("/api/scenarios/validate", json={"yaml_text": config_text})
    assert result.status_code == 200
    assert result.json()["config_sha256"] == config_sha256(validate_yaml(config_text))
    assert result.json()["station_count"] == 3
    assert client.post("/api/scenarios/validate", json={"yaml_text": "invalid"}).status_code == 422
    response = client.post(
        "/api/scenarios/validate",
        content=b'{"yaml_text": "\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert (
        client.post(
            "/api/scenarios/validate", json={"yaml_text": config_text, "extra": 1}
        ).status_code
        == 422
    )
    assert client.get("/api/runs").status_code == 404
    assert client.get("/api/truth").status_code == 404
