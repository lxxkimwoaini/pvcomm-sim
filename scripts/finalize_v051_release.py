"""Append verified guides and W60 compatibility evidence without changing a release."""

import argparse
import importlib
import tempfile
from pathlib import Path
from typing import Any

from pvcomm.pipeline.dataset import code_record, read_json, write_json
from pvcomm.storage.parquet import source_hash


def checked_hashes(root: Path, hashes: dict[str, str]) -> None:
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or source_hash(path) != expected:
            raise ValueError(f"release changed: {relative}")


def comparable_rows(folder: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    pq = importlib.import_module("pyarrow.parquet")
    tables = {
        name: pq.read_table(folder / f"{name}.parquet").to_pylist()
        for name in ("features", "labels", "rules", "index")
    }
    result = {}
    for i, row in enumerate(tables["index"]):
        key = row["run_id"], row["station_id"], row["window_end_utc_ns"]
        if key in result:
            raise ValueError("duplicate W60 window identity")
        result[key] = {
            name: {k: v for k, v in tables[name][i].items() if k != "sample_id"}
            for name in ("features", "labels", "rules")
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--previous-dataset", type=Path, required=True)
    args = parser.parse_args()
    root, previous = args.dataset_dir, args.previous_dataset
    publication = root / "publication"
    if publication.exists():
        raise FileExistsError("publication appendix already exists")
    release = read_json(root / "release.json")
    if release["code_dirty"]:
        raise ValueError("processing source was not frozen")
    checked_hashes(root, release["files"])
    checked_hashes(previous, read_json(previous / "release.json")["hashes"])
    old, new = comparable_rows(previous / "features-M2"), comparable_rows(root / "W60/features-M2")
    if old != new:
        raise ValueError("W60 features/labels/rules differ from preserved control")
    docs = Path(__file__).resolve().parents[1] / "docs"
    with tempfile.TemporaryDirectory(prefix=".publication-", dir=root) as temporary:
        stage = Path(temporary) / "publication"
        stage.mkdir()
        for source, target in (
            ("v051-dataset.md", "TRAINING.md"),
            ("data-dictionary-v051.md", "data-dictionary-v051.md"),
            ("data-dictionary-v05.md", "data-dictionary-v05.md"),
        ):
            (stage / target).write_bytes((docs / source).read_bytes())
        write_json(
            stage / "compatibility.json",
            dict(
                passed=True,
                windows_compared=len(old),
                comparison="exact W60 feature, label and rule values "
                "keyed by run/station/endpoint; "
                "new opaque sample IDs and partitions intentionally separate",
                old_release_hashes_verified=True,
            ),
        )
        write_json(
            stage / "manifest.json",
            dict(
                base_release_sha256=source_hash(root / "release.json"),
                old_release_sha256=source_hash(previous / "release.json"),
                files={p.name: source_hash(p) for p in stage.iterdir()},
                **code_record(),
            ),
        )
        stage.rename(publication)
    print(f"verified {len(old)} unchanged W60 windows; appended {publication}")


if __name__ == "__main__":
    main()
