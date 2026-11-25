"""One validation and canonicalization service shared by CLI and Web."""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from pvcomm.config.models import FoundationConfig

MAX_CONFIG_BYTES = 128 * 1024


class ConfigError(ValueError):
    """An external configuration failed validation."""


class UniqueKeyLoader(yaml.SafeLoader):
    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node | None:
        if self.check_event(AliasEvent):
            raise ConfigError("YAML aliases are not supported in configuration")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ConfigError(f"duplicate YAML key: {key}")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError as exc:
                raise ConfigError("YAML mapping keys must be scalar") from exc
        return result


def check_yaml_tree(value: object, depth: int = 0, budget: list[int] | None = None) -> None:
    """Bound alias expansion and nesting before serializing user input."""
    if budget is None:
        budget = [4096]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise ConfigError("YAML is too deeply nested or has too many expanded nodes")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ConfigError("YAML object keys must be strings")
            check_yaml_tree(child, depth + 1, budget)
    elif isinstance(value, list):
        for child in value:
            check_yaml_tree(child, depth + 1, budget)


def validate_yaml(text: str) -> FoundationConfig:
    try:
        if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError(f"config exceeds {MAX_CONFIG_BYTES} bytes")
        data = yaml.load(text, Loader=UniqueKeyLoader)
        check_yaml_tree(data)
        # JSON conversion normalizes YAML sequences for immutable tuple fields.
        payload = json.dumps(data, allow_nan=False)
        return FoundationConfig.model_validate_json(payload)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        lines = [
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in errors[:8]
        ]
        if len(errors) > 8:
            lines.append(f"另有 {len(errors) - 8} 项错误，请补全后重新校验。")
        raise ConfigError(f"配置校验失败（{len(errors)} 项）：\n" + "\n".join(lines)) from exc
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise ConfigError(str(exc)) from exc


def load_config(path: Path) -> FoundationConfig:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ConfigError(f"config exceeds {MAX_CONFIG_BYTES} bytes")
        return validate_yaml(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read configuration: {exc}") from exc


def canonical_json(config: FoundationConfig) -> str:
    return json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def config_sha256(config: FoundationConfig) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
