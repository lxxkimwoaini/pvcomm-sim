"""Execute or resume a frozen finite matrix through the controlled lab runner."""

import argparse
import json
from pathlib import Path

from pvcomm.config.batch import BatchConfig
from pvcomm.scenario.batch import run_batch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = BatchConfig.model_validate_json(args.matrix.read_bytes())
    state = run_batch(config, args.output, resume=args.resume)
    print(json.dumps(state))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
