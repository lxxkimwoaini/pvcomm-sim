"""Build one immutable prerelease from retained runs; never starts simulation."""

import argparse
import json
from pathlib import Path

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.experiments.baselines import benchmark_dataset
from pvcomm.pipeline.dataset import inventory_runs, split_dataset
from pvcomm.pipeline.features import build_features
from pvcomm.qc.dataset import qc_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    policy = DatasetPolicy.model_validate_json(args.config.read_bytes())
    stages = [
        inventory_runs(args.root, args.dataset_dir),
        split_dataset(args.dataset_dir, policy),
        build_features(args.dataset_dir),
        qc_dataset(args.dataset_dir),
    ]
    if not stages[-1]["passed"]:
        raise RuntimeError("dataset QC failed; baselines not started")
    for mode in ("M0", "M1", "M2"):
        stages.append(benchmark_dataset(args.dataset_dir, mode))
    print(json.dumps(stages, sort_keys=True))


if __name__ == "__main__":
    main()
