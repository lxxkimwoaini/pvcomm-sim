"""CLI arguments and presentation; no duplicated validation or run logic."""

import argparse
import json
import signal
import sys
from pathlib import Path

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, load_config
from pvcomm.config.lab import load_v01_config, v01_config_sha256
from pvcomm.logging import create_logger
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.scenario.v01_runner import V01Runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pvcomm")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-config", help="validate foundation YAML; no simulation"
    )
    validate.add_argument("--config", type=Path, required=True)
    validate_lab = commands.add_parser("validate-lab-config", help="validate V0.1 IEC 104 lab YAML")
    validate_lab.add_argument("--config", type=Path, required=True)
    parse = commands.add_parser("parse", help="parse a V0.1 PCAP with TShark")
    parse.add_argument("--run-dir", type=Path, required=True)
    run = commands.add_parser("run", help="run the V0.1 real IEC 104 local lab")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--isolated", action="store_true", help="owned Linux namespace P0/P1 lab")
    for name in ("label", "qc", "split", "features", "benchmark"):
        reserved = commands.add_parser(name, help="not implemented in V0.0")
        reserved.add_argument("arguments", nargs=argparse.REMAINDER)
    args, unknown = parser.parse_known_args(argv)
    if args.command == "validate-lab-config":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        try:
            config = load_v01_config(args.config)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "config_sha256": v01_config_sha256(config),
                    "schema_version": config.schema_version,
                    "station_count": len(config.stations),
                }
            )
        )
        return 0
    if args.command == "run":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def request_cancellation(signum: int, frame: object) -> None:
            raise KeyboardInterrupt("laboratory run cancelled")

        signal.signal(signal.SIGTERM, request_cancellation)
        try:
            lab_config = load_v01_config(args.config)
            if args.isolated:
                from pvcomm.scenario.isolated_runner import run_isolated

                run_dir = run_isolated(lab_config, args.config, args.output_root)
            else:
                run_dir = V01Runner(lab_config, args.config, args.output_root).run()
        except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as exc:
            logger = create_logger("pvcomm.cli", sys.stderr)
            logger.error(str(exc), extra={"event": "run_failed"})
            return 1
        except KeyboardInterrupt:
            print("run cancelled; partial evidence retained", file=sys.stderr)
            return 130
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
        print(json.dumps({"status": "completed", "run_dir": str(run_dir)}))
        return 0
    if args.command == "parse":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        try:
            summary = parse_run(args.run_dir)
        except (OSError, ValueError, RuntimeError) as exc:
            logger = create_logger("pvcomm.cli", sys.stderr)
            logger.error(str(exc), extra={"event": "parse_failed"})
            return 1
        print(json.dumps(summary, sort_keys=True))
        return 0
    if args.command != "validate-config":
        print(f"{args.command}: not implemented in V0.0; no experiment executed", file=sys.stderr)
        return 3
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")
    logger = create_logger("pvcomm.cli", sys.stderr)
    try:
        foundation_config = load_config(args.config)
    except ConfigError as exc:
        logger.error(str(exc), extra={"event": "config_invalid"})
        return 2
    digest = config_sha256(foundation_config)
    logger.info(
        "configuration validated",
        extra={"run_id": foundation_config.run_id, "event": "config_valid"},
    )
    print(
        json.dumps(
            {
                "valid": True,
                "config_sha256": digest,
                "schema_version": foundation_config.schema_version,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
