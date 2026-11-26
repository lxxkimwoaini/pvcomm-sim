"""Write one explicit small development scene; never overwrite an input."""

import argparse
from pathlib import Path

import yaml

from pvcomm.config.normal_scenes import SCENE_CODES, normal_scenario


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=SCENE_CODES, required=True)
    parser.add_argument("--variant", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scene = normal_scenario(args.scene, args.variant)
    with args.output.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(scene.model_dump(mode="json"), stream, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
