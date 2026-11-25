"""Write explicit development assumptions, never formal dataset parameters."""

import argparse
from pathlib import Path

import yaml

from pvcomm.config.presets import configuration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", type=int, choices=(3, 10, 20, 30), required=True)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--interruption", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(
            configuration(args.sites, args.duration, interruption=args.interruption),
            stream,
            allow_unicode=True,
            sort_keys=False,
        )


if __name__ == "__main__":
    main()
