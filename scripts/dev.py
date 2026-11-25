"""Thin, cross-platform development commands. Never manages lab resources."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(f"required executable missing: {command[0]}")
    subprocess.run([executable, *command[1:]], cwd=ROOT, check=True)


def execute(target: str) -> int:
    uv = ["uv", "run", "--frozen"]
    pnpm = ["pnpm", "--dir", "frontend"]
    commands = {
        "bootstrap": [
            ["uv", "--version"],
            ["node", "--version"],
            ["pnpm", "--version"],
            ["uv", "sync", "--frozen"],
            [*pnpm, "install", "--frozen-lockfile"],
        ],
        "lint": [[*uv, "ruff", "check", "."], [*uv, "ruff", "format", "--check", "."]],
        "format": [[*uv, "ruff", "check", "--fix", "."], [*uv, "ruff", "format", "."]],
        "typecheck": [[*uv, "mypy"]],
        "test": [[*uv, "pytest", "-m", "not lab"]],
        "schemas": [
            [*uv, "python", "scripts/generate_schemas.py", "--check"],
            [*pnpm, "types:check"],
        ],
        "web-test": [
            [*pnpm, "lint"],
            [*pnpm, "format:check"],
            [*pnpm, "typecheck"],
            [*pnpm, "test"],
        ],
        "web-build": [[*pnpm, "build"]],
        "dev-web": [[*pnpm, "dev"]],
    }
    if target == "test-lab":
        if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
            print("blocked: authorized Linux lab requires PVCOMM_ALLOW_LAB=1", file=sys.stderr)
            return 3
        # Reuse the active interpreter, including .venv-wsl. A nested uv run
        # would otherwise select the Windows .venv on this shared checkout.
        run([sys.executable, "-m", "pytest", "-m", "lab"])
        return 0
    if target == "check":
        for child in ("lint", "typecheck", "test", "schemas", "web-test", "web-build"):
            code = execute(child)
            if code:
                return code
        return 0
    for command in commands[target]:
        run(command)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target",
        choices=[
            "bootstrap",
            "lint",
            "format",
            "typecheck",
            "test",
            "schemas",
            "web-test",
            "web-build",
            "dev-web",
            "test-lab",
            "check",
        ],
    )
    args = parser.parse_args()
    try:
        return execute(args.target)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
