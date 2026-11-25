"""Write a non-overwriting offline infrastructure report from real evidence."""

import argparse
import json
from pathlib import Path

from pvcomm.qc.performance import performance_report, web_performance_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--web-dir", type=Path)
    args = parser.parse_args()
    report = performance_report(args.run_dir)
    if args.web_dir is not None:
        report["web_measurement"] = web_performance_report(args.web_dir)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
