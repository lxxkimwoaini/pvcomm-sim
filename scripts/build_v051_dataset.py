"""Build registered multiscale development data from retained evidence only."""

import argparse
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pvcomm.config.dataset import DatasetPolicy
from pvcomm.experiments.baselines import benchmark_dataset
from pvcomm.experiments.dataset_report import release_report
from pvcomm.pipeline.dataset import code_record, inventory_runs, split_dataset, write_json
from pvcomm.pipeline.features import build_features
from pvcomm.qc.dataset import qc_dataset
from pvcomm.storage.parquet import source_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/v0.5.1-dataset.json"))
    parser.add_argument(
        "--previous-dataset", type=Path, default=Path("/var/lib/pvcomm/datasets/v05_prerelease_s3")
    )
    args = parser.parse_args()
    policy = DatasetPolicy.model_validate_json(args.config.read_bytes())
    if policy.schema_version != "0.5.1-dataset.1":
        raise ValueError("use the registered V0.5.1 configuration")
    start = time.monotonic()
    root = args.dataset_dir
    stages = []
    initial: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        ("inventory", lambda: inventory_runs(args.root, root)),
        ("split", lambda: split_dataset(root, policy)),
    )
    for name, action in initial:
        result = action()
        stages.append(dict(stage=name, report=result))
        print(f"{name} complete {time.monotonic() - start:.3f}s", flush=True)
    for scale in ("W10", "W30", "W60"):
        build_features(root, scale=scale)
        qc = qc_dataset(root, scale=scale)
        print(f"{scale} QC {qc['passed']} {time.monotonic() - start:.3f}s", flush=True)
        if not qc["passed"]:
            raise RuntimeError(f"{scale} independent QC failed; evidence retained")
        for mode in ("M0", "M1", "M2"):
            benchmark_dataset(root, mode, scale)
            print(f"{scale}/{mode} baseline complete {time.monotonic() - start:.3f}s", flush=True)
        for ablation in ("without-rule-proxies", "without-peer"):
            benchmark_dataset(root, "M2", scale, ablation)
            print(f"{scale}/{ablation} complete {time.monotonic() - start:.3f}s", flush=True)
    release_report(root, args.previous_dataset)
    write_json(
        root / "release.json",
        dict(
            version="0.5.1",
            evaluation_use="development_only",
            stages=stages,
            elapsed_s=time.monotonic() - start,
            **code_record(),
            files={
                str(p.relative_to(root)): source_hash(p)
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )


if __name__ == "__main__":
    main()
