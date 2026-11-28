"""Evaluate frozen pair candidates; an unmatched valid run remains retained."""

import argparse
import json
from pathlib import Path

from pvcomm.config.similarity import CalibratedSimilarity, SimilarityPolicy
from pvcomm.qc.similarity import evaluate_pair_similarity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--pair-dir", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = SimilarityPolicy.model_validate_json(args.policy.read_bytes())
    calibration = CalibratedSimilarity.model_validate_json(args.calibration.read_bytes())
    with args.output.open("x", encoding="utf-8") as stream:
        for directory in args.pair_dir:
            report = evaluate_pair_similarity(directory, policy, calibration)
            stream.write(json.dumps(dict(directory=str(directory), report=report)) + "\n")
            stream.flush()


if __name__ == "__main__":
    main()
