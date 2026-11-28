"""Write an implemented single-fault development configuration without overwrite."""

import argparse
from pathlib import Path

from pvcomm.config.fault_scenes import a01_scenario, a02_scenario, terminal_scenario
from pvcomm.config.faults import FaultScope, GlobalScope, RegionScope, StationScope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("station", "region", "global"), required=True)
    parser.add_argument("--stations", nargs="+")
    parser.add_argument("--region")
    parser.add_argument(
        "--fault-type", choices=("A01", "A02", "A04", "A05", "A06", "A08"), default="A01"
    )
    parser.add_argument("--strength", type=int)
    parser.add_argument("--duration", type=int, choices=(5, 20, 60))
    parser.add_argument("--delay-ms", type=int, choices=(200, 1000, 3000))
    parser.add_argument("--direction", choices=("master_to_rtu", "rtu_to_master"))
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
    if args.fault_type == "A01":
        if (
            args.duration is None
            or args.delay_ms is not None
            or args.direction is not None
            or args.strength is not None
        ):
            parser.error("A01 requires --duration and no delay parameters")
        config = a01_scenario(scope, args.duration, args.variant)
    elif args.fault_type == "A02":
        if (
            args.duration is not None
            or args.delay_ms is None
            or args.direction is None
            or args.strength is not None
        ):
            parser.error("A02 requires --delay-ms and --direction; duration is the preset 30s")
        config = a02_scenario(scope, args.delay_ms, args.direction, args.variant)
    else:
        if args.strength is None or any(
            value is not None for value in (args.duration, args.delay_ms, args.direction)
        ):
            parser.error("terminal mechanisms require only --strength and --scope")
        config = terminal_scenario(args.fault_type, scope, args.strength, args.variant)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(config.model_dump_json(indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
