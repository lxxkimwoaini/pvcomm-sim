"""Generate contracts from Python; --check compares without writing files."""

import argparse
import json
from pathlib import Path

from pvcomm.config import FoundationConfig
from pvcomm.config.lab import V01Config
from pvcomm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contracts = {
        "foundation.schema.json": FoundationConfig.model_json_schema(),
        "v01-lab.schema.json": V01Config.model_json_schema(),
        "openapi.json": create_app().openapi(),
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
