"""Add missing versioned labels/QC to retained real runs; never run a simulator."""

import argparse
import json
from pathlib import Path

from pvcomm.pipeline.dataset import read_json, run_directories
from pvcomm.pipeline.labels import label_run, load_label_inputs
from pvcomm.qc.labels import qc_labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("preparation report already exists")
    outcomes = []
    for root in sorted(run_directories(args.root)):
        manifest = read_json(root / "manifest.json")
        config_path = root / "power_config.json"
        if (
            not config_path.is_file()
            or manifest.get("status") != "completed"
            or manifest.get("code_dirty") is not False
            or manifest.get("capture_topology") != "isolated_master_P0_and_RTU_P1"
        ):
            continue
        config = read_json(config_path)
        if len(config["protocol"]["stations"]) not in (3, 10):
            continue
        if (root / "qc/v04-label-qc-v4/report.json").exists():
            continue
        try:
            with load_label_inputs(root) as inputs:
                if not inputs.report.get("passed"):
                    continue
            if not (root / "labels/v04-label-v3").exists():
                label_run(root)
            result = qc_labels(root)
            outcomes.append(dict(path=str(root), label_qc_passed=result["passed"]))
        except (OSError, ValueError, KeyError) as exc:
            outcomes.append(dict(path=str(root), error=str(exc)))
    # Inspecting happens in the separate catalogue command, preserving failures.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(outcomes, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                prepared=len(outcomes),
                passed=sum(r.get("label_qc_passed", False) for r in outcomes),
            )
        )
    )


if __name__ == "__main__":
    main()
