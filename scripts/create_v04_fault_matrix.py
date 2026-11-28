"""Write a fixed acceptance matrix; refuse to replace any existing file."""

import argparse
import hashlib
from pathlib import Path

from pvcomm.config.coverage import fault_coverage_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("scale-10", "scale-30", "coverage"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = fault_coverage_matrix(args.stage)
    payload = (config.model_dump_json(indent=2) + "\n").encode()
    with args.output.open("xb") as stream:
        stream.write(payload)
    print(f"{len(config.tasks)} tasks; SHA256 {hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    main()
