"""Read-only scientific recheck plus new versioned reports; no new lab runs."""

import argparse
import json
from pathlib import Path

from pvcomm.qc.maintenance_acceptance import qc_grouped_recovery


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        for root in args.run_dir:
            report = qc_grouped_recovery(root)
            stream.write(json.dumps(dict(root=str(root), report=report)) + "\n")
            stream.flush()
            if not report["passed"]:
                raise RuntimeError(f"grouped recovery recheck failed: {root}")


if __name__ == "__main__":
    main()
