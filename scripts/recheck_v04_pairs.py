"""Versioned offline recheck of retained pairs; never starts or overwrites a run."""

import argparse
import json
from pathlib import Path

from pvcomm.pipeline.labels import label_run
from pvcomm.qc.labels import qc_labels
from pvcomm.qc.pairs import qc_pair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-dir", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        for directory in args.pair_dir:
            ledger = json.loads((directory / "pair-manifest.json").read_bytes())
            for member in ledger["members"]:
                if (
                    member["run_dir"] != member["run_id"]
                    or Path(member["run_id"]).name != member["run_id"]
                ):
                    raise ValueError("unsafe pair member path")
                root = directory / member["run_dir"]
                label_run(root)
                report = qc_labels(root)
                stream.write(json.dumps(dict(kind="member", root=str(root), report=report)) + "\n")
                stream.flush()
                if not report["passed"]:
                    raise RuntimeError(f"label recheck failed: {root}")
            report = qc_pair(directory)
            stream.write(json.dumps(dict(kind="pair", root=str(directory), report=report)) + "\n")
            stream.flush()
            if not report["passed"]:
                raise RuntimeError(f"pair recheck failed: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
