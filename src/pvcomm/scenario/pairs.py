"""Pair orchestration composes the existing isolated runner, parser and QC."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pvcomm.config.faults import instantiate_fault
from pvcomm.config.pairs import PairConfig
from pvcomm.config.scenes import instantiate_scenario
from pvcomm.pipeline.iec104_parser import parse_run
from pvcomm.pipeline.labels import label_run
from pvcomm.qc.fault import qc_fault
from pvcomm.qc.labels import qc_labels
from pvcomm.qc.schedule import qc_schedule
from pvcomm.scenario.isolated_runner import run_isolated
from pvcomm.storage.parquet import export_parquet
from pvcomm.storage.power_parquet import export_power_parquet


def run_pair(config: PairConfig, output_root: Path) -> Path:
    directory = output_root / config.pair_id
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "pair.json"
    source.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    state: dict[str, Any] = dict(
        pair_id=config.pair_id,
        source_group_id=config.normal.source_group_id,
        shared_sha256=config.shared_sha256(),
        status="running",
        members=[],
    )

    def save() -> None:
        stage = directory / ".pair-manifest.tmp"
        stage.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        stage.replace(directory / "pair-manifest.json")

    save()
    try:
        for member in ("normal", "changed"):
            identity = f"run_{uuid.uuid4().hex}"
            fault = instantiate_fault(config.changed, identity) if member == "changed" else None
            scenario = fault.background if fault else instantiate_scenario(config.normal, identity)
            path = directory / f"input_{len(state['members'])}.json"
            path.write_text((fault or scenario).model_dump_json(indent=2) + "\n", encoding="utf-8")
            record = dict(member=member, run_id=identity, run_dir=identity, status="running")
            state["members"].append(record)
            save()
            root = run_isolated(
                scenario.simulation.protocol,
                path,
                directory,
                power=scenario.simulation,
                scenario=scenario,
                fault=fault,
            )
            parse_run(root)
            export_parquet(root)
            export_power_parquet(root)
            report = qc_fault(root) if fault else qc_schedule(root)
            record["mechanism_or_normal_qc"] = report["passed"]
            if not report["passed"]:
                raise RuntimeError(f"member QC failed; evidence retained at {root}")
            label_run(root)
            label_report = qc_labels(root)
            record["label_qc"] = label_report["passed"]
            if not label_report["passed"]:
                raise RuntimeError(f"member label QC failed; evidence retained at {root}")
            record["status"] = "completed"
            save()
        from pvcomm.qc.pairs import qc_pair

        pair_report = qc_pair(directory)
        state["pair_qc"] = pair_report["passed"]
        state["status"] = "completed" if pair_report["passed"] else "qc_failed"
    except BaseException as exc:
        state["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save()
    return directory
