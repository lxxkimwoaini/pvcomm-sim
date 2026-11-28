"""Write a configured pair; output identities/configuration are Oracle only."""

import argparse
from pathlib import Path

from pvcomm.config.pairs import development_pair


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair-type", choices=("P03", "P04", "P06", "P03_recovery"), required=True)
    parser.add_argument("--pair-id", required=True)
    parser.add_argument("--variant", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configuration = development_pair(args.pair_type, args.pair_id, args.variant)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(configuration.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
