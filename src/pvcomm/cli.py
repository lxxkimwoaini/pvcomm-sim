"""CLI arguments and presentation; no duplicated validation or run logic."""

import argparse
import json
import signal
import sys
import uuid
from pathlib import Path

from pvcomm import __version__
from pvcomm.config import ConfigError, config_sha256, load_config
from pvcomm.config.lab import V01Config, load_lab_document, load_v01_config, v01_config_sha256
from pvcomm.config.power import power_document
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
    validate_lab = commands.add_parser("validate-lab-config", help="validate IEC 104 lab YAML")
    validate_lab.add_argument("--config", type=Path, required=True)
    validate_power = commands.add_parser(
        "validate-power-config", help="validate V0.2/V0.3 power YAML"
    )
    validate_power.add_argument("--config", type=Path, required=True)
    validate_weather = commands.add_parser(
        "validate-weather-source", help="validate a versioned offline historical source descriptor"
    )
    validate_weather.add_argument("--config", type=Path, required=True)
    parse = commands.add_parser("parse", help="parse retained PCAP with TShark")
    parse.add_argument("--run-dir", type=Path, required=True)
    parse.add_argument("--parquet", action="store_true", help="also export versioned Arrow tables")
    qc = commands.add_parser("qc", help="independent protocol and physical verification")
    qc.add_argument("--run-dir", type=Path, required=True)
    qc.add_argument(
        "--profile",
        choices=("normal", "experiment", "schedule", "fault", "labels"),
        default="normal",
    )
    storage = commands.add_parser("storage-audit", help="read-only inventory and capacity budget")
    storage.add_argument("--run-dir", type=Path, required=True)
    storage.add_argument("--policy", type=Path, required=True)
    archive = commands.add_parser("archive-lossless", help="verify gzip archive; retain source")
    archive.add_argument("--source", type=Path, required=True)
    archive.add_argument("--destination", type=Path, required=True)
    run = commands.add_parser("run", help="run configured real IEC 104 isolated/local lab")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--isolated", action="store_true", help="owned Linux namespace P0/P1 lab")
    validate_scene = commands.add_parser(
        "validate-scenario", help="validate and expand an explicit V0.4 business schedule"
    )
    validate_scene.add_argument("--config", type=Path, required=True)
    run_scene = commands.add_parser(
        "run-scenario", help="run an explicit V0.4 scene in the owned isolated Linux lab"
    )
    run_scene.add_argument("--config", type=Path, required=True)
    run_scene.add_argument("--output-root", type=Path, required=True)
    run_scene.add_argument("--run-id", help="optional unique identity; existing output is refused")
    validate_fault = commands.add_parser(
        "validate-fault-scenario", help="validate an Oracle-only single-fault configuration"
    )
    validate_fault.add_argument("--config", type=Path, required=True)
    run_fault = commands.add_parser(
        "run-fault", help="run a single fault only in the owned isolated Linux lab"
    )
    run_fault.add_argument("--config", type=Path, required=True)
    run_fault.add_argument("--output-root", type=Path, required=True)
    run_fault.add_argument("--run-id", help="optional unique identity; existing output is refused")
    run_pair = commands.add_parser(
        "run-pair", help="run and audit a frozen pair in the isolated lab"
    )
    run_pair.add_argument("--config", type=Path, required=True)
    run_pair.add_argument("--output-root", type=Path, required=True)
    label = commands.add_parser("label", help="derive evidence-linked Oracle labels; requires QC")
    label.add_argument("--run-dir", type=Path, required=True)
    for name in ("split", "features", "benchmark"):
        reserved = commands.add_parser(name, help="not implemented in this development version")
        reserved.add_argument("arguments", nargs=argparse.REMAINDER)
    args, unknown = parser.parse_known_args(argv)
    if args.command == "label":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.pipeline.labels import label_run

        try:
            label_report = label_run(args.run_dir)
        except (OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(label_report, sort_keys=True))
        return 0
    if args.command == "validate-weather-source":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.config.weather import HistoricalInput
        from pvcomm.weather import open_historical_input

        try:
            weather_source = HistoricalInput.model_validate_json(args.config.read_bytes())
            open_historical_input(weather_source).sample(0)
        except (OSError, ValueError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "source_id": weather_source.source_id,
                    "adapter": weather_source.adapter,
                    "sha256": weather_source.sha256,
                }
            )
        )
        return 0
    if args.command in ("storage-audit", "archive-lossless"):
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.storage.retention import (
            RetentionPolicy,
            archive_lossless,
            inventory,
            storage_budget,
        )

        try:
            if args.command == "storage-audit":
                if not args.run_dir.is_dir():
                    raise ValueError("run directory does not exist")
                policy = RetentionPolicy.model_validate_json(args.policy.read_bytes())
                result = {"inventory": inventory(args.run_dir), "budget": storage_budget(policy)}
            else:
                result = archive_lossless(args.source, args.destination)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "validate-power-config":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        try:
            power_config = power_document(args.config)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "schema_version": power_config.schema_version,
                    "weather_sha256": power_config.weather.sha256,
                }
            )
        )
        return 0
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
    if args.command == "validate-scenario":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.config.scenes import canonical_hash, load_scenario

        try:
            template = load_scenario(args.config)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "schema_version": template.schema_version,
                    "config_sha256": canonical_hash(template.model_dump(mode="json")),
                    "expanded_plan": [
                        t.model_dump(mode="json") for t in template.expanded_schedule()
                    ],
                    "scope": "Oracle_configuration_not_M2",
                }
            )
        )
        return 0
    if args.command == "validate-fault-scenario":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.config.faults import load_fault_scenario

        try:
            fault_template = load_fault_scenario(args.config)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "schema_version": fault_template.schema_version,
                    "target_stations": fault_template.target_stations(),
                    "scope": "Oracle_configuration_not_M2",
                }
            )
        )
        return 0
    if args.command in ("run", "run-scenario", "run-fault", "run-pair"):
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def request_cancellation(signum: int, frame: object) -> None:
            raise KeyboardInterrupt("laboratory run cancelled")

        signal.signal(signal.SIGTERM, request_cancellation)
        try:
            if args.command == "run-pair":
                from pvcomm.config.pairs import PairConfig
                from pvcomm.scenario.pairs import run_pair as execute_pair

                pair = PairConfig.model_validate_json(args.config.read_bytes())
                pair_dir = execute_pair(pair, args.output_root)
                pair_state = json.loads((pair_dir / "pair-manifest.json").read_bytes())
                print(json.dumps({"status": pair_state["status"], "pair_dir": str(pair_dir)}))
                return 0 if pair_state["status"] == "completed" else 1
            scenario = None
            fault = None
            isolated = args.command in ("run-scenario", "run-fault") or args.isolated
            if args.command == "run-fault":
                from pvcomm.config.faults import instantiate_fault, load_fault_scenario

                fault = instantiate_fault(
                    load_fault_scenario(args.config), args.run_id or f"run_{uuid.uuid4().hex}"
                )
                scenario = fault.background
            if args.command == "run-scenario":
                from pvcomm.config.scenes import instantiate_scenario, load_scenario

                scenario = instantiate_scenario(
                    load_scenario(args.config), args.run_id or f"run_{uuid.uuid4().hex}"
                )
            document = load_lab_document(args.config) if scenario is None else scenario.simulation
            power = document if not isinstance(document, V01Config) else None
            lab_config = power.protocol if power is not None else load_v01_config(args.config)
            if power is not None and not isolated:
                raise ValueError("physical simulation requires --isolated")
            if isolated:
                from pvcomm.scenario.isolated_runner import run_isolated

                run_dir = run_isolated(
                    lab_config,
                    args.config,
                    args.output_root,
                    power=power,
                    scenario=scenario,
                    fault=fault,
                )
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
            if args.parquet:
                from pvcomm.storage.parquet import export_parquet

                summary["parquet"] = export_parquet(args.run_dir)
                if (args.run_dir / "power_config.json").exists():
                    from pvcomm.storage.power_parquet import export_power_parquet

                    summary["power_parquet"] = export_power_parquet(args.run_dir)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            logger = create_logger("pvcomm.cli", sys.stderr)
            logger.error(str(exc), extra={"event": "parse_failed"})
            return 1
        print(json.dumps(summary, sort_keys=True))
        return 0
    if args.command == "qc":
        if unknown:
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        from pvcomm.qc.v01 import qc_run

        try:
            if args.profile == "labels":
                from pvcomm.qc.labels import qc_labels

                report = qc_labels(args.run_dir)
            elif args.profile == "fault":
                from pvcomm.qc.fault import qc_fault

                report = qc_fault(args.run_dir)
            elif args.profile == "schedule":
                from pvcomm.qc.schedule import qc_schedule

                report = qc_schedule(args.run_dir)
            elif args.profile == "experiment":
                from pvcomm.qc.experiment import qc_experiment

                report = qc_experiment(args.run_dir)
            elif (args.run_dir / "power_config.json").exists():
                from pvcomm.qc.power import qc_power

                report = qc_power(args.run_dir)
            else:
                report = qc_run(args.run_dir)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(report, sort_keys=True))
        return 0 if report["passed"] else 1
    if args.command != "validate-config":
        print(
            f"{args.command}: not implemented in this development version; no experiment executed",
            file=sys.stderr,
        )
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
