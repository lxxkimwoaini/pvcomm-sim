"""Audit finalized batches without changing retained runs, labels or thresholds."""

import argparse
import json
from pathlib import Path

from pvcomm.qc.batch import audit_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_batch(args.batch, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in ("attempts", "queue_coverage")}))


if __name__ == "__main__":
    main()
