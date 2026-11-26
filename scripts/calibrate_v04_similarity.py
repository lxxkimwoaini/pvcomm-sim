"""Run the frozen independent normal calibration before any new pair evaluation."""

import argparse
import json
from pathlib import Path

from pvcomm.config.similarity import SimilarityPolicy
from pvcomm.scenario.calibration import run_calibration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = SimilarityPolicy.model_validate_json(args.policy.read_bytes())
    print(json.dumps(run_calibration(policy, args.output)))


if __name__ == "__main__":
    main()
