"""CLI arguments and presentation; no duplicated validation or run logic."""

import argparse
import json
import sys
from pathlib import Path

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, load_config
from pvcomm.logging import create_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pvcomm")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-config", help="validate foundation YAML; no simulation"
    )
    validate.add_argument("--config", type=Path, required=True)
    for name in ("run", "parse", "label", "qc", "split", "features", "benchmark"):
        reserved = commands.add_parser(name, help="not implemented in V0.0")
        reserved.add_argument("arguments", nargs=argparse.REMAINDER)
    args, unknown = parser.parse_known_args(argv)
    if args.command != "validate-config":
        print(f"{args.command}: not implemented in V0.0; no experiment executed", file=sys.stderr)
        return 3
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")
    logger = create_logger("pvcomm.cli", sys.stderr)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error(str(exc), extra={"event": "config_invalid"})
        return 2
    digest = config_sha256(config)
    logger.info("configuration validated", extra={"run_id": config.run_id, "event": "config_valid"})
    print(
        json.dumps(
            {"valid": True, "config_sha256": digest, "schema_version": config.schema_version}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
