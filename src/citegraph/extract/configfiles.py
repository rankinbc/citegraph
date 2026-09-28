"""Config key NAMES from JSON, YAML, env example files and pyproject scripts. Values are never kept."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator
from typing import Any, cast

import yaml

from citegraph.models import ConfigKey, EntryPoint, ExtractResult

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class ConfigFileExtractor:
    language = "config"

    def extract(self, rel_path: str, source: bytes) -> ExtractResult:
        name = rel_path.rsplit("/", 1)[-1]
        text = source.decode("utf-8", "replace")
        try:
            if name == "pyproject.toml":
                return _pyproject(text)
            if name.startswith(".env."):
                return _env_example(text)
            if name.endswith(".json"):
                return _json(text)
            if name.endswith((".yaml", ".yml")):
                return _yaml(text, compose=name.startswith("docker-compose"))
        except (ValueError, yaml.YAMLError):  # JSONDecodeError and TOMLDecodeError subclass ValueError
            return ExtractResult(parse_error=True)
        return ExtractResult()


def _find_key_line(lines: list[str], key: str, start: int) -> int:
    needle = json.dumps(key)
    for index in range(start, len(lines)):
        if needle in lines[index]:
            return index + 1
    return start + 1


def _json(text: str) -> ExtractResult:
    data: object = json.loads(text)
    lines = text.splitlines()
    result = ExtractResult()

    def walk(obj: object, prefix: list[str], start: int) -> None:
        if not isinstance(obj, dict):
            return
        for key, value in cast(dict[str, object], obj).items():
            line = _find_key_line(lines, key, start)
            path = [*prefix, key]
            if isinstance(value, dict):
                walk(cast(dict[str, object], value), path, line - 1)
            else:
                result.config_keys.append(ConfigKey(key_path=":".join(path), line=line, origin="json"))
            start = line

    walk(data, [], 0)
    return result


def _mapping_get(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def _yaml_walk(node: yaml.Node, prefix: list[str], result: ExtractResult) -> None:
    if not isinstance(node, yaml.MappingNode):
        return
    for key_node, value_node in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        path = [*prefix, str(key_node.value)]
        if isinstance(value_node, yaml.MappingNode):
            _yaml_walk(value_node, path, result)
        else:
            result.config_keys.append(
                ConfigKey(key_path=".".join(path), line=key_node.start_mark.line + 1, origin="yaml")
            )


def _compose_env(doc: yaml.Node, result: ExtractResult) -> None:
    services = _mapping_get(doc, "services")
    if not isinstance(services, yaml.MappingNode):
        return
    for _, service in cast(list[tuple[yaml.Node, yaml.Node]], services.value):
        env = _mapping_get(service, "environment")
        if isinstance(env, yaml.MappingNode):
            for key_node, _value in cast(list[tuple[yaml.Node, yaml.Node]], env.value):
                result.config_keys.append(
                    ConfigKey(key_path=str(key_node.value), line=key_node.start_mark.line + 1, origin="yaml")
                )
        elif isinstance(env, yaml.SequenceNode):
            for item in cast(list[yaml.Node], env.value):
                if isinstance(item, yaml.ScalarNode):
                    name = str(item.value).split("=", 1)[0].strip()  # the value part is dropped here
                    if name:
                        result.config_keys.append(
                            ConfigKey(key_path=name, line=item.start_mark.line + 1, origin="yaml")
                        )


def _compose_all(text: str) -> Iterator[yaml.Node | None]:
    return cast(
        "Iterator[yaml.Node | None]",
        yaml.compose_all(text, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
    )


def _yaml(text: str, compose: bool) -> ExtractResult:
    result = ExtractResult()
    for doc in _compose_all(text):
        if doc is None:
            continue
        if compose:
            _compose_env(doc, result)
        else:
            _yaml_walk(doc, [], result)
    return result


def _env_example(text: str) -> ExtractResult:
    result = ExtractResult()
    for number, line in enumerate(text.splitlines(), start=1):
        match = _ENV_LINE.match(line)
        if match:
            result.config_keys.append(ConfigKey(key_path=match.group(1), line=number, origin="env-example"))
    return result


def _pyproject(text: str) -> ExtractResult:
    data: dict[str, Any] = tomllib.loads(text)
    scripts: dict[str, Any] = data.get("project", {}).get("scripts", {})
    lines = text.splitlines()
    result = ExtractResult()
    for name, target in scripts.items():
        pattern = re.compile(rf'^\s*"?{re.escape(name)}"?\s*=')
        line = next((i for i, text_line in enumerate(lines, start=1) if pattern.match(text_line)), 1)
        result.entry_points.append(
            EntryPoint(kind="console-script", name=name, target=str(target), line=line)
        )
    return result
