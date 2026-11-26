"""Write a new A01 development configuration; never overwrite evidence."""

import argparse
from pathlib import Path

from pvcomm.config.fault_scenes import a01_scenario
from pvcomm.config.faults import FaultScope, GlobalScope, RegionScope, StationScope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("station", "region", "global"), required=True)
    parser.add_argument("--stations", nargs="+")
    parser.add_argument("--region")
    parser.add_argument("--duration", type=int, choices=(5, 20, 60), required=True)
    parser.add_argument("--variant", type=int, choices=(0, 1), default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scope: FaultScope
    if args.scope == "station" and args.stations and args.region is None:
        scope = StationScope(kind="station", station_ids=tuple(args.stations))
    elif args.scope == "region" and args.region and args.stations is None:
        scope = RegionScope(kind="region", region_id=args.region)
    elif args.scope == "global" and args.region is None and args.stations is None:
        scope = GlobalScope(kind="global")
    else:
        parser.error("scope requires only its own station/region identities")
    config = a01_scenario(scope, args.duration, args.variant)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(config.model_dump_json(indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
