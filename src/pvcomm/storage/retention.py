"""Read-only storage budgets and verified lossless archives. Never deletes sources."""

import gzip
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from pvcomm.config.lab import V01Contract


class RetentionPolicy(V01Contract):
    schema_version: Literal["0.4-retention.1"]
    level: Literal["full_evidence", "verified_archive", "derived_rebuildable"]
    delete_sources: Literal[False] = False
    compression: Literal["gzip_lossless"] = "gzip_lossless"
    formal_hours: Annotated[int, Field(strict=True, ge=1)] = 720
    retained_bytes_per_hour: Annotated[int, Field(strict=True, gt=0)] = 1_383_250_188
    capacity_margin: Annotated[float, Field(ge=1)] = 1.5
    concurrent_instances: Annotated[int, Field(strict=True, ge=1)] = 1
    measured_parse_temporary_bytes: Annotated[int, Field(strict=True, gt=0)] = 11_275_084_964


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def storage_budget(policy: RetentionPolicy) -> dict[str, int | str | bool]:
    import math

    retained = math.ceil(
        policy.retained_bytes_per_hour * policy.formal_hours * policy.capacity_margin
    )
    return {
        "retained_capacity_bytes": retained,
        "separate_full_backup_bytes": retained,
        "temporary_lower_bound_bytes": policy.measured_parse_temporary_bytes
        * policy.concurrent_instances,
        "temporary_peak_measured": False,
        "concurrency_throughput_validated": False,
        "basis": "V0.3 measured retained bytes; PDML single-file lower bound, not total peak",
    }


def archive_lossless(source: Path, destination: Path) -> dict[str, object]:
    """Create a new archive directory, verify decompressed bytes, keep original.

    A committed directory contains both archive and manifest. Interrupted
    staging is not a valid archive. An existing destination is never replaced.
    """
    if source.is_symlink() or not source.is_file():
        raise ValueError("archive requires a regular source file")
    if destination.exists():
        raise FileExistsError(f"archive destination exists: {destination}")
    original_hash = sha256_file(source)
    original_size = source.stat().st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=destination.parent) as temp:
        stage = Path(temp) / "archive"
        stage.mkdir()
        compressed = stage / "payload.gz"
        with (
            source.open("rb") as stream,
            compressed.open("xb") as output,
            gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped,
        ):
            shutil.copyfileobj(stream, zipped)
        with gzip.open(compressed, "rb") as restored:
            restored_hash = hashlib.file_digest(restored, "sha256").hexdigest()
        if restored_hash != original_hash or sha256_file(source) != original_hash:
            raise ValueError("lossless archive verification failed or source changed")
        record = {
            "schema_version": "0.4-lossless-archive.1",
            "source_name": source.name,
            "source_sha256": original_hash,
            "source_bytes": original_size,
            "archive_sha256": sha256_file(compressed),
            "archive_bytes": compressed.stat().st_size,
            "restored_sha256": restored_hash,
            "source_retained": True,
        }
        (stage / "manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        stage.rename(destination)
    return record


def inventory(root: Path) -> dict[str, object]:
    """Size projection only; raw files are not read, changed, archived or removed."""
    rows = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rows.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size})
            total += path.stat().st_size
    return {"files": rows, "bytes": total, "read_only": True}
