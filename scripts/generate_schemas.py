"""Generate contracts from Python; --check compares without writing files."""

import argparse
import json
from pathlib import Path

from pvcomm.config import FoundationConfig
from pvcomm.config.faults import FaultScenarioConfig
from pvcomm.config.lab import V01Config
from pvcomm.config.multisite import V03Config
from pvcomm.config.power import V02Config
from pvcomm.config.scenes import ScenarioConfig
from pvcomm.config.weather import HistoricalInput
from pvcomm.storage.parquet import APDU_FIELDS, EVENT_FIELDS, OBJECT_FIELDS, PACKET_FIELDS, REQUIRED
from pvcomm.storage.power_parquet import TABLES as POWER_TABLES
from pvcomm.storage.retention import RetentionPolicy
from pvcomm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contracts = {
        "foundation.schema.json": FoundationConfig.model_json_schema(),
        "v01-lab.schema.json": V01Config.model_json_schema(),
        "v02-power.schema.json": V02Config.model_json_schema(),
        "v03-multisite.schema.json": V03Config.model_json_schema(),
        "v04-scenario.schema.json": ScenarioConfig.model_json_schema(),
        "v04-fault.schema.json": FaultScenarioConfig.model_json_schema(),
        "v04-weather-source.schema.json": HistoricalInput.model_json_schema(),
        "v04-retention.schema.json": RetentionPolicy.model_json_schema(),
        "openapi.json": create_app().openapi(),
        "v02-power-parquet.contract.json": {
            "schema_version": "0.2-power-evidence.2",
            "scope": "Oracle",
            "tables": {
                name: {
                    field: {"arrow_type": kind, "nullable": False} for field, kind in fields.items()
                }
                for name, fields in POWER_TABLES.items()
            },
        },
        "v01-parquet.contract.json": {
            "schema_version": "0.1-protocol-evidence",
            "timezone": "UTC",
            "utc_unit": "ns",
            "tables": {
                name: {
                    field: {"arrow_type": kind, "nullable": field not in REQUIRED}
                    for field, kind in fields.items()
                }
                for name, fields in (
                    ("packets", PACKET_FIELDS),
                    ("iec104_apdu", APDU_FIELDS),
                    ("iec104_objects", OBJECT_FIELDS),
                    ("endpoint_events", EVENT_FIELDS),
                )
            },
        },
    }
    for name, contract in contracts.items():
        path = ROOT / "schemas" / name
        content = json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                print(f"outdated contract: {path}")
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
