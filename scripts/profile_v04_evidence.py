"""Read-only comparison on retained real evidence; write a new profile directory."""

import argparse
import json
import subprocess
import time
import tracemalloc
from pathlib import Path

from pvcomm.config.scenes import canonical_hash
from pvcomm.domain.labels import EventLabel
from pvcomm.pipeline.labels import VERSION, build_labels, file_sha256, load_label_inputs
from pvcomm.qc.labels import audit_label_records
from pvcomm.storage.parquet import verify_parquet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("list", "indexed"), required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.run_dir
    label_path = root / "labels" / VERSION / "event_labels.jsonl"
    retained = [
        EventLabel.model_validate_json(line) for line in label_path.read_text().splitlines()
    ]
    tracemalloc.start()
    began = time.perf_counter()
    with load_label_inputs(root, indexed=args.mode == "indexed") as inputs:
        hashes = inputs.hashes.copy()
        regenerated = build_labels(inputs)
        audit = audit_label_records(inputs, regenerated)
        temporary_bytes = (
            sum(
                p.stat().st_size
                for p in Path(inputs.index.temporary.name).rglob("*")
                if p.is_file()
            )
            if inputs.index
            else 0
        )
    label_elapsed = time.perf_counter() - began
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    began = time.perf_counter()
    arrow = verify_parquet(root)
    repository = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"]))
    result = dict(
        profile_version="v04-evidence-profile-v1",
        mode=args.mode,
        run_id=json.loads((root / "manifest.json").read_bytes())["run_id"],
        code_version=sha,
        code_dirty=dirty,
        source_hashes=hashes,
        retained_label_sha256=file_sha256(label_path),
        regenerated_labels_sha256=canonical_hash([r.model_dump(mode="json") for r in regenerated]),
        labels_equal=regenerated == retained,
        independent_audit=audit,
        label_elapsed_s=label_elapsed,
        python_traced_peak_bytes=peak,
        index_retained_bytes=temporary_bytes,
        index_cleaned=not list(root.glob(".label-index-*")),
        parquet_qc=arrow,
        parquet_qc_elapsed_s=time.perf_counter() - began,
        measurement_scope="single read-only recheck, traced Python allocation excludes native RSS",
    )
    result["passed"] = bool(
        not dirty
        and result["labels_equal"]
        and audit["passed"]
        and arrow["passed"]
        and result["index_cleaned"]
    )
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
