"""Compare one versus two real serial lab queues without changing production concurrency."""

import argparse
import json
from pathlib import Path

from pvcomm.scenario.concurrency_profile import profile_concurrency


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = profile_concurrency(args.output)
    print(json.dumps(report))
    return 0 if report["evaluation_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
