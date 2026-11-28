"""Resume the two frozen serial batches; delegate simulation/QC to the existing runner."""

import argparse
from pathlib import Path
from typing import Any

from pvcomm.config.batch import BatchConfig
from pvcomm.pipeline.dataset import code_record, read_json
from pvcomm.scenario.batch import run_batch, save_json
from pvcomm.storage.parquet import source_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = read_json(args.plan / "protocol.json")
    if (
        code_record() != {k: protocol[k] for k in ("code_version", "code_dirty")}
        or protocol["code_dirty"]
    ):
        raise ValueError("production requires the original clean frozen source")
    args.output.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = dict(status="running", plan=str(args.plan), stages={})
    for name in ("independent_test", "normal_24h"):
        path = args.plan / f"{name}.json"
        if source_hash(path) != protocol["batch_sha256"][name]:
            raise ValueError("frozen task list changed")
        batch = BatchConfig.model_validate_json(path.read_bytes())
        destination = args.output / name
        result = run_batch(batch, destination, resume=(destination / "manifest.json").exists())
        state["stages"][name] = result["status"]
        save_json(args.output / "production.json", state)
    state["status"] = (
        "completed"
        if all(v == "completed" for v in state["stages"].values())
        else "completed_with_failures"
    )
    save_json(args.output / "production.json", state)


if __name__ == "__main__":
    main()
