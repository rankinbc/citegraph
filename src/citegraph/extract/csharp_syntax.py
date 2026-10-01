"""Pure helpers over tree-sitter C# syntax nodes: names, declared types, string literals, modifiers."""

from __future__ import annotations

from typing import Literal

from tree_sitter import Node

_WRAPPED_TYPES = frozenset({"nullable_type", "array_type", "ref_type", "scoped_type"})


def text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", "replace")


def line(node: Node) -> int:
    return node.start_point[0] + 1


def end_line(node: Node) -> int:
    row, col = node.end_point[0], node.end_point[1]
    return max(1, row + (1 if col > 0 else 0))


def join(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def simple(node: Node | None) -> str | None:
    """`Foo` or `Foo<T>` -> `Foo`."""
    if node is None:
        return None
    if node.type == "identifier":
        return text(node)
    if node.type == "generic_name":
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        return text(ident) if ident is not None else None
    return None


def type_name(node: Node | None) -> str | None:
    """A declared type without generic arguments, nullable or array markers; None for `var` and built-ins."""
    if node is None:
        return None
    kind = node.type
    if kind in ("identifier", "generic_name"):
        return simple(node)
    if kind == "qualified_name":
        name = type_name(node.child_by_field_name("name"))
        if name is None:
            return None
        qualifier = type_name(node.child_by_field_name("qualifier"))
        return join(qualifier or "", name)
    if kind == "alias_qualified_name":  # global::A -> A
        return type_name(node.child_by_field_name("name"))
    if kind in _WRAPPED_TYPES:
        return type_name(node.child_by_field_name("type"))
    return None


def dotted(node: Node) -> str | None:
    kind = node.type
    if kind in ("identifier", "generic_name"):
        return simple(node)
    if kind == "predefined_type":
        return text(node)
    if kind in ("qualified_name", "alias_qualified_name"):
        return type_name(node)
    if kind == "member_access_expression":
        expression = node.child_by_field_name("expression")
        name = simple(node.child_by_field_name("name"))
        base = dotted(expression) if expression is not None else None
        if base is None or name is None:
            return None
        return f"{base}.{name}"
    return None


def string_value(node: Node | None) -> str | None:
    if node is not None and node.type == "argument":
        node = node.named_children[0] if node.named_children else None
    if node is None:
        return None
    if node.type == "string_literal":
        parts = node.named_children
        if any(c.type != "string_literal_content" for c in parts):
            return None
        return "".join(text(c) for c in parts)
    if node.type == "verbatim_string_literal":
        return text(node)[2:-1].replace('""', '"')
    return None


def first_arg(call: Node) -> str | None:
    args = call.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    return string_value(first)


def modifiers(node: Node) -> set[str]:
    return {text(c) for c in node.named_children if c.type == "modifier"}


def visibility(node: Node, default_public: bool) -> Literal["public", "private"]:
    mods = modifiers(node)
    if mods & {"public", "protected", "internal"}:
        return "public"
    if "private" in mods:
        return "private"
    return "public" if default_public else "private"


def first_namespace(root: Node) -> str:
    for child in root.named_children:
        if child.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            return type_name(child.child_by_field_name("name")) or ""
    return ""
