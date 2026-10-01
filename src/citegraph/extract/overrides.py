"""citegraph.overrides.yaml: hand-written links between symbols that static analysis cannot see."""

from __future__ import annotations

from typing import cast

import yaml

from citegraph.models import ExtractResult, Reference

OVERRIDES_FILE = "citegraph.overrides.yaml"


def _scalar(node: yaml.Node | None) -> str | None:
    if isinstance(node, yaml.ScalarNode) and isinstance(node.value, str) and node.value.strip():
        return node.value.strip()
    return None


def _field(entry: yaml.MappingNode, key: str) -> yaml.Node | None:
    for key_node, value_node in cast(list[tuple[yaml.Node, yaml.Node]], entry.value):
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


class OverridesExtractor:
    language = "overrides"

    def extract(self, rel_path: str, source: bytes) -> ExtractResult:
        """One `curated` reference per entry: `from_qualified` is the caller, `to_name` the callee, both resolved
        later by qualified name. Anything malformed makes the whole file a parse error with no references."""
        try:
            root = cast(
                "yaml.Node | None",
                yaml.compose(source.decode("utf-8", "replace"), Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
            )
        except yaml.YAMLError:
            return ExtractResult(parse_error=True)
        if root is None:
            return ExtractResult()
        edges = _field(root, "edges") if isinstance(root, yaml.MappingNode) else None
        if not isinstance(edges, yaml.SequenceNode):
            return ExtractResult(parse_error=True)
        result = ExtractResult()
        for entry in cast(list[yaml.Node], edges.value):
            if not isinstance(entry, yaml.MappingNode):
                return ExtractResult(parse_error=True)
            source_name, target = _scalar(_field(entry, "from")), _scalar(_field(entry, "to"))
            kind = _scalar(_field(entry, "kind")) or "call"
            if source_name is None or target is None or kind != "call":
                return ExtractResult(parse_error=True)
            result.references.append(
                Reference(
                    from_qualified=source_name,
                    to_name=target,
                    kind="curated",
                    line=entry.start_mark.line + 1,
                    note=_scalar(_field(entry, "note")),
                )
            )
        return result
