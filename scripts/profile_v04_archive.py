"""Archive a finalized small experiment, verify every file, retain all sources."""

import argparse
import json
import time
from pathlib import Path

from pvcomm.storage.retention import archive_lossless, sha256_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.run_dir.resolve(), args.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("archive output must be separate from the retained run")
    manifest = json.loads((source / "manifest.json").read_bytes())
    if manifest["status"] != "completed" or manifest["cleanup_errors"]:
        raise ValueError("profile requires a finalized small acceptance run")
    output.mkdir(parents=True, exist_ok=False)
    before = {
        p.relative_to(source).as_posix(): sha256_file(p)
        for p in sorted(source.rglob("*"))
        if p.is_file()
    }
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("profile does not archive symlink sources")
    records = []
    began = time.monotonic()
    # Each existing archive operation verifies decompression and the retained source.
    for relative, digest in before.items():
        result = archive_lossless(source / relative, output / "files" / relative)
        if result["source_sha256"] != digest:
            raise ValueError("retained source changed before compression")
        records.append(dict(path=relative, **result))
    after = {
        p.relative_to(source).as_posix(): sha256_file(p)
        for p in sorted(source.rglob("*"))
        if p.is_file()
    }
    result = dict(
        profile_version="v04-archive-profile-v1",
        run_id=manifest["run_id"],
        files=records,
        elapsed_s=time.monotonic() - began,
        source_bytes=sum(int(r["source_bytes"]) for r in records),
        payload_bytes=sum(int(r["archive_bytes"]) for r in records),
        archive_directory_bytes=sum(p.stat().st_size for p in output.rglob("*") if p.is_file()),
        all_sources_retained=before == after,
        passed=before == after,
        scope="single small acceptance run; not a 30-site throughput or parse peak measurement",
    )
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not result["passed"]:
        raise ValueError("source inventory changed during archive")
    print(json.dumps({k: v for k, v in result.items() if k != "files"}))


if __name__ == "__main__":
    main()
