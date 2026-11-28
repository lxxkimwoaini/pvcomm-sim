"""Freeze the twelve predeclared pair trials after independent calibration."""

import argparse
from pathlib import Path

from pvcomm.config.batch import BatchConfig, independent_pair_templates
from pvcomm.config.similarity import CalibratedSimilarity, SimilarityPolicy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix-id", required=True)
    args = parser.parse_args()
    config = BatchConfig.model_validate(
        dict(
            matrix_id=args.matrix_id,
            matching=dict(
                policy=SimilarityPolicy.model_validate_json(args.policy.read_bytes()),
                calibration=CalibratedSimilarity.model_validate_json(args.calibration.read_bytes()),
            ),
            tasks=[
                dict(kind="pair", task_id=f"trial_{i:02d}", max_attempts=1, pair=pair)
                for i, pair in enumerate(independent_pair_templates())
            ],
        )
    )
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(config.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
