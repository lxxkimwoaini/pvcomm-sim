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
    parser.add_argument(
        "--measurements", type=Path, help="new external Linux resource sample directory"
    )
    args = parser.parse_args()
    config = BatchConfig.model_validate_json(args.matrix.read_bytes())
    if args.measurements is None:
        state = run_batch(config, args.output, resume=args.resume)
    else:
        from pvcomm.scenario.batch_resources import BatchResources

        with BatchResources(args.output, args.measurements) as resources:
            state = run_batch(config, args.output, resume=args.resume)
        if resources.errors:
            raise RuntimeError(f"resource sampling incomplete: {resources.errors}")
    print(json.dumps(state))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
