"""Consume finite Web requests through the existing controlled Linux batch runner."""

import argparse
import json
from pathlib import Path

from pvcomm.scenario.requests import execute_requests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--maximum-jobs", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(execute_requests(args.root, resume=args.resume, maximum_jobs=args.maximum_jobs))
    )


if __name__ == "__main__":
    main()
