"""Config key NAMES from JSON, YAML, env example files and pyproject scripts. Values are never kept."""

from __future__ import annotations

import json
import re
import tomllib
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator
from typing import Any, cast

import yaml

from citegraph.models import ConfigKey, EntryPoint, ExtractResult

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:)?')
_YAML_MERGE = "tag:yaml.org,2002:merge"
# YAML key/value pairs walked per file; past this the file is skipped as a parse error. Each node is walked once,
# so only a file written with this many keys reaches it.
MAX_YAML_KEYS = 100_000


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


class _JsonKeys:
    """Every object key of a JSON text with its line and bracket depth, found in one pass over the text.

    JSON strings are the only place a quote can appear, so scanning string tokens left to right finds each one
    exactly once; a string followed by `:` is a key. Keys are matched decoded, so a non-ASCII key is found whether it
    is written escaped or as UTF-8. The depth (open brackets) keeps a key from matching a same-named key nested
    elsewhere.
    """

    def __init__(self, text: str) -> None:
        self.line_starts = [0, *(m.end() for m in re.finditer("\n", text))]
        self.lines: list[int] = []
        self.positions: dict[tuple[str, int], list[int]] = defaultdict(list)
        depth = 0
        last = 0
        for match in _JSON_STRING.finditer(text):
            gap = text[last : match.start()]
            depth += gap.count("{") + gap.count("[") - gap.count("}") - gap.count("]")
            last = match.end()
            if match.group(2) is None:
                continue
            raw = match.group(1)
            key = cast(str, json.loads(f'"{raw}"')) if "\\" in raw else raw
            self.positions[(key, depth)].append(len(self.lines))
            self.lines.append(bisect_right(self.line_starts, match.start()))

    def find(self, key: str, depth: int, after: int) -> int | None:
        """The first key token after token `after` with this key at this depth."""
        found = self.positions.get((key, depth), [])
        index = bisect_right(found, after)
        return found[index] if index < len(found) else None

    def line(self, token: int) -> int:
        return self.lines[token] if token >= 0 else 1


def _json(text: str) -> ExtractResult:
    data: object = json.loads(text)
    keys = _JsonKeys(text)
    result = ExtractResult()

    def walk(obj: dict[str, object], prefix: list[str], depth: int, after: int) -> int:
        """Keys of `obj` (found after token `after`); returns the last token used, so siblings search past it."""
        for key, value in obj.items():
            token = keys.find(key, depth, after)
            line = keys.line(token if token is not None else after)
            after = token if token is not None else after
            path = [*prefix, key]
            if isinstance(value, dict):
                after = walk(cast(dict[str, object], value), path, depth + 1, after)
            else:
                result.config_keys.append(ConfigKey(key_path=":".join(path), line=line, origin="json"))
        return after

    if isinstance(data, dict):
        walk(cast(dict[str, object], data), [], 1, -1)
    return result


def _mapping_get(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in cast(list[tuple[yaml.Node, yaml.Node]], node.value):
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def _yaml_walk(root: yaml.Node, result: ExtractResult, budget: list[int]) -> bool:
    """Dotted key paths of a document, in document order. False when the file's walk budget runs out.

    Each mapping node is walked once. An alias to a mapping already walked (`prod: *defaults`) is reported as its
    own key, not expanded again, and a merge (`<<: *defaults`) adds nothing: those keys were reported where they
    are written. An alias bomb (anchors aliased inside anchors, exponential when expanded) so costs one step per
    node written in the file.
    """
    if not isinstance(root, yaml.MappingNode):
        return True
    seen = {id(root)}
    stack: list[tuple[Iterator[tuple[yaml.Node, yaml.Node]], list[str]]] = [
        (iter(cast(list[tuple[yaml.Node, yaml.Node]], root.value)), [])
    ]
    while stack:
        pairs, prefix = stack[-1]
        pair = next(pairs, None)
        if pair is None:
            stack.pop()
            continue
        budget[0] -= 1
        if budget[0] < 0:
            return False
        key_node, value_node = pair
        if not isinstance(key_node, yaml.ScalarNode):
            continue
        if key_node.tag == _YAML_MERGE:  # an inline mapping merged here is walked as this mapping's own keys
            merged = value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
            for node in reversed(cast(list[yaml.Node], merged)):
                if isinstance(node, yaml.MappingNode) and id(node) not in seen:
                    seen.add(id(node))
                    stack.append((iter(cast(list[tuple[yaml.Node, yaml.Node]], node.value)), prefix))
            continue
        path = [*prefix, str(key_node.value)]
        if isinstance(value_node, yaml.MappingNode) and id(value_node) not in seen:
            seen.add(id(value_node))
            stack.append((iter(cast(list[tuple[yaml.Node, yaml.Node]], value_node.value)), path))
        else:
            result.config_keys.append(
                ConfigKey(key_path=".".join(path), line=key_node.start_mark.line + 1, origin="yaml")
            )
    return True


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
    budget = [MAX_YAML_KEYS]
    for doc in _compose_all(text):
        if doc is None:
            continue
        if compose:
            _compose_env(doc, result)
        elif not _yaml_walk(doc, result, budget):
            return ExtractResult(parse_error=True)  # skipped whole, counted with the parse errors
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
