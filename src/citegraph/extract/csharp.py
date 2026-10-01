"""C# extractor: symbols, references with declared receiver types, using directives, config reads and entry points."""

from __future__ import annotations

from typing import Literal

import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Node, Parser

from citegraph.models import ConfigKey, EntryPoint, ExtractResult, ImportFact, Reference, Symbol

_LANGUAGE = Language(tscsharp.language())
_parser_instance: Parser | None = None

_TYPE_KINDS: dict[str, Literal["class", "interface"]] = {
    "class_declaration": "class",
    "record_declaration": "class",
    "struct_declaration": "class",
    "enum_declaration": "class",
    "interface_declaration": "interface",
}
_SCOPES = frozenset({"block", "lambda_expression", "anonymous_method_expression"})
_WRAPPED_TYPES = frozenset({"nullable_type", "array_type", "ref_type", "scoped_type"})
_SECTION_METHODS = frozenset({"GetSection", "GetRequiredSection"})
_CONFIG_METHODS = _SECTION_METHODS | {"GetValue"}
_NOT_CALLS = frozenset({"nameof"})


def _parser() -> Parser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = Parser(_LANGUAGE)
    return _parser_instance


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", "replace")


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    row, col = node.end_point[0], node.end_point[1]
    return max(1, row + (1 if col > 0 else 0))


def _join(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _simple(node: Node | None) -> str | None:
    """`Foo` or `Foo<T>` -> `Foo`."""
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type == "generic_name":
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        return _text(ident) if ident is not None else None
    return None


def _type_name(node: Node | None) -> str | None:
    """A declared type without generic arguments, nullable or array markers; None for `var` and built-ins."""
    if node is None:
        return None
    kind = node.type
    if kind in ("identifier", "generic_name"):
        return _simple(node)
    if kind == "qualified_name":
        name = _type_name(node.child_by_field_name("name"))
        if name is None:
            return None
        qualifier = _type_name(node.child_by_field_name("qualifier"))
        return _join(qualifier or "", name)
    if kind == "alias_qualified_name":  # global::A -> A
        return _type_name(node.child_by_field_name("name"))
    if kind in _WRAPPED_TYPES:
        return _type_name(node.child_by_field_name("type"))
    return None


def _dotted(node: Node) -> str | None:
    kind = node.type
    if kind in ("identifier", "generic_name"):
        return _simple(node)
    if kind == "predefined_type":
        return _text(node)
    if kind in ("qualified_name", "alias_qualified_name"):
        return _type_name(node)
    if kind == "member_access_expression":
        expression = node.child_by_field_name("expression")
        name = _simple(node.child_by_field_name("name"))
        base = _dotted(expression) if expression is not None else None
        if base is None or name is None:
            return None
        return f"{base}.{name}"
    return None


def _string_value(node: Node | None) -> str | None:
    if node is not None and node.type == "argument":
        node = node.named_children[0] if node.named_children else None
    if node is None:
        return None
    if node.type == "string_literal":
        parts = node.named_children
        if any(c.type != "string_literal_content" for c in parts):
            return None
        return "".join(_text(c) for c in parts)
    if node.type == "verbatim_string_literal":
        return _text(node)[2:-1].replace('""', '"')
    return None


def _first_arg(call: Node) -> str | None:
    args = call.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    return _string_value(first)


def _modifiers(node: Node) -> set[str]:
    return {_text(c) for c in node.named_children if c.type == "modifier"}


def _visibility(node: Node, default_public: bool) -> Literal["public", "private"]:
    mods = _modifiers(node)
    if mods & {"public", "protected", "internal"}:
        return "public"
    if "private" in mods:
        return "private"
    return "public" if default_public else "private"


def _first_namespace(root: Node) -> str:
    for child in root.named_children:
        if child.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            return _type_name(child.child_by_field_name("name")) or ""
    return ""


class _Visitor:
    def __init__(self, stem: str) -> None:
        self.stem = stem
        self.result = ExtractResult()
        self.frames: list[dict[str, str]] = []
        self.interfaces: set[str] = set()
        self.top_level_line: int | None = None

    def run(self, root: Node) -> ExtractResult:
        namespace = _first_namespace(root)
        module = namespace or self.stem
        self.result.module = namespace
        self.result.symbols.append(
            Symbol(
                kind="module",
                name=module.rsplit(".", 1)[-1],
                qualified_name=module,
                line_start=1,
                line_end=_end_line(root),
            )
        )
        self.frames.append({})
        self._walk(root, "", module, None)
        if self.top_level_line is not None:
            self.result.entry_points.append(
                EntryPoint(kind="program-main", name=module, line=self.top_level_line)
            )
        return self.result

    # --- scopes -----------------------------------------------------------------------------------------

    def _declare(self, name: str, type_node: Node | None) -> None:
        declared = _type_name(type_node)
        if name and declared:
            self.frames[-1][name] = declared

    def _declared(self, name: str) -> str | None:
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return None

    def _declare_params(self, params: Node | None) -> None:
        if params is None:
            return
        for param in params.named_children:
            if param.type == "parameter":
                self._declare(_text(param.child_by_field_name("name")), param.child_by_field_name("type"))

    def _scoped(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        self.frames.append({})
        self._declare_params(node.child_by_field_name("parameters"))
        self._walk(node, namespace, owner, type_q)
        self.frames.pop()

    # --- traversal --------------------------------------------------------------------------------------

    def _walk(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        for child in node.named_children:
            if child.type == "file_scoped_namespace_declaration":  # its declarations follow as siblings
                namespace = _join(namespace, _type_name(child.child_by_field_name("name")) or "")
                continue
            self._visit(child, namespace, owner, type_q)

    def _visit(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        kind = node.type
        if kind == "namespace_declaration":
            inner = _join(namespace, _type_name(node.child_by_field_name("name")) or "")
            body = node.child_by_field_name("body")
            if body is not None:
                self._walk(body, inner, owner, type_q)
        elif kind in _TYPE_KINDS:
            self._type(node, namespace, type_q)
        elif kind in ("method_declaration", "constructor_declaration") and type_q is not None:
            self._method(node, namespace, type_q)
        elif kind == "local_function_statement" or kind in _SCOPES:
            self._scoped(node, namespace, owner, type_q)
        elif kind == "global_statement":
            if self.top_level_line is None:
                self.top_level_line = _line(node)
            self._walk(node, namespace, owner, type_q)
        elif kind == "using_directive":
            self._using(node)
        elif kind == "variable_declaration":
            self._variables(node, namespace, owner, type_q)
        elif kind in ("declaration_expression", "declaration_pattern"):
            self._declare(_text(node.child_by_field_name("name")), node.child_by_field_name("type"))
        elif kind == "invocation_expression":
            self._invocation(node, owner)
            self._walk(node, namespace, owner, type_q)
        elif kind == "object_creation_expression":
            created = _type_name(node.child_by_field_name("type"))
            if created is not None:
                self._ref(owner, created, "instantiate", node)
            self._walk(node, namespace, owner, type_q)
        elif kind == "element_access_expression":
            self._config_indexer(node, owner)
            self._walk(node, namespace, owner, type_q)
        else:
            self._walk(node, namespace, owner, type_q)

    # --- declarations -----------------------------------------------------------------------------------

    def _type(self, node: Node, namespace: str, outer: str | None) -> None:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{outer}.{name}" if outer else _join(namespace, name)
        kind = _TYPE_KINDS[node.type]
        if kind == "interface":
            self.interfaces.add(qualified)
        self.result.symbols.append(
            Symbol(
                kind=kind,
                name=name,
                qualified_name=qualified,
                line_start=_line(node),
                line_end=_end_line(node),
                visibility=_visibility(node, default_public=outer is None or outer in self.interfaces),
            )
        )
        body = node.child_by_field_name("body")
        frame: dict[str, str] = {}
        self.frames.append(frame)
        for child in node.named_children:
            if child.type == "base_list":
                self._bases(child, qualified)
            elif child.type == "parameter_list":  # primary constructor parameters are in scope in the body
                self._declare_params(child)
        if body is not None:
            self._members(body)
            self._walk(body, namespace, qualified, qualified)
        self.frames.pop()

    def _bases(self, base_list: Node, qualified: str) -> None:
        for base in base_list.named_children:
            if base.type == "primary_constructor_base_type":
                base = base.child_by_field_name("type")
            name = _type_name(base)
            if base is not None and name is not None:
                self._ref(qualified, name, "inherit", base)

    def _members(self, body: Node) -> None:
        """Declared types of fields and properties, visible to every member of the type."""
        for member in body.named_children:
            if member.type == "field_declaration":
                decl = next((c for c in member.named_children if c.type == "variable_declaration"), None)
                if decl is not None:
                    type_node = decl.child_by_field_name("type")
                    for declarator in decl.named_children:
                        if declarator.type == "variable_declarator":
                            self._declare(_text(declarator.child_by_field_name("name")), type_node)
            elif member.type == "property_declaration":
                self._declare(_text(member.child_by_field_name("name")), member.child_by_field_name("type"))

    def _method(self, node: Node, namespace: str, type_q: str) -> None:
        mods = _modifiers(node)
        if node.type == "constructor_declaration":
            name = ".cctor" if "static" in mods else ".ctor"
        else:
            name = _text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{type_q}.{name}"
        params = node.child_by_field_name("parameters")
        count = sum(1 for c in params.named_children if c.type == "parameter") if params is not None else 0
        self.result.symbols.append(
            Symbol(
                kind="method",
                name=name,
                qualified_name=qualified,
                line_start=_line(node),
                line_end=_end_line(node),
                visibility=_visibility(node, default_public=type_q in self.interfaces),
                param_count=count,
            )
        )
        if name == "Main" and "static" in mods:
            self.result.entry_points.append(EntryPoint(kind="program-main", name=qualified, line=_line(node)))
        self._scoped(node, namespace, qualified, type_q)

    def _variables(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        type_node = node.child_by_field_name("type")
        declared = _type_name(type_node)
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value = next((c for c in declarator.named_children if c != name_node), None)
            if declared is not None:
                self._declare(_text(name_node), type_node)
            elif value is not None and value.type == "object_creation_expression":  # var x = new T()
                self._declare(_text(name_node), value.child_by_field_name("type"))
            if value is None:
                continue
            if value.type == "implicit_object_creation_expression" and declared is not None:  # T x = new()
                self._ref(owner, declared, "instantiate", value)
            self._visit(value, namespace, owner, type_q)

    def _using(self, node: Node) -> None:
        tokens = {c.type for c in node.children if not c.is_named}
        alias = node.child_by_field_name("name")
        target_node = next((c for c in node.named_children if c != alias), None)
        target = _type_name(target_node)
        if not target:
            return
        if alias is not None:
            local = f"*global={_text(alias)}" if "global" in tokens else _text(alias)
        elif "static" in tokens:
            local = "*global-static" if "global" in tokens else "*static"
        else:
            local = "*global" if "global" in tokens else "*"
        self.result.imports.append(ImportFact(local_name=local, target=target, line=_line(node)))

    # --- references -------------------------------------------------------------------------------------

    def _ref(
        self,
        owner: str,
        to_name: str,
        kind: Literal["call", "inherit", "instantiate"],
        node: Node,
        receiver_type: str | None = None,
    ) -> None:
        self.result.references.append(
            Reference(
                from_qualified=owner,
                to_name=to_name,
                kind=kind,
                line=_line(node),
                receiver_type=receiver_type,
            )
        )

    def _member_target(self, expression: Node | None, name: str) -> tuple[str, str | None]:
        if expression is None:
            return f"?.{name}", None
        if expression.type in ("this", "base"):
            return f"{expression.type}.{name}", None
        if expression.type == "identifier":
            declared = self._declared(_text(expression))
            if declared is not None:
                return name, declared
        if expression.type == "member_access_expression":
            inner = expression.child_by_field_name("expression")
            if inner is not None and inner.type == "this":  # this._field.Foo()
                declared = self._declared(_simple(expression.child_by_field_name("name")) or "")
                return (name, declared) if declared is not None else (f"?.{name}", None)
        dotted = _dotted(expression)
        return (f"{dotted}.{name}", None) if dotted is not None else (f"?.{name}", None)

    def _call_target(self, fn: Node) -> tuple[str, str | None] | None:
        if fn.type in ("identifier", "generic_name"):
            name = _simple(fn)
            return (name, None) if name else None
        if fn.type == "member_access_expression":
            name = _simple(fn.child_by_field_name("name"))
            return self._member_target(fn.child_by_field_name("expression"), name) if name else None
        if fn.type == "conditional_access_expression":  # a?.Foo()
            binding = next((c for c in fn.named_children if c.type == "member_binding_expression"), None)
            name = _simple(binding.child_by_field_name("name")) if binding is not None else None
            return self._member_target(fn.child_by_field_name("condition"), name) if name else None
        return None

    def _invocation(self, node: Node, owner: str) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        target = self._call_target(fn)
        if target is None or target[0] in _NOT_CALLS:
            return
        self._ref(owner, target[0], "call", node, receiver_type=target[1])
        self._config_call(node, fn, owner)

    # --- config reads -----------------------------------------------------------------------------------

    def _config_key(self, key: str | None, node: Node, owner: str) -> None:
        if key:
            self.result.config_keys.append(
                ConfigKey(key_path=key, line=_line(node), origin="code-read", reader_qualified=owner)
            )

    def _section_prefix(self, node: Node | None) -> str | None:
        """`config.GetSection("A").GetSection("B")` -> `A:B` for the receiver of a chained config read."""
        if node is None or node.type != "invocation_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_access_expression":
            return None
        if _simple(fn.child_by_field_name("name")) not in _SECTION_METHODS:
            return None
        key = _first_arg(node)
        if not key:
            return None
        parent = self._section_prefix(fn.child_by_field_name("expression"))
        return f"{parent}:{key}" if parent else key

    def _config_call(self, node: Node, fn: Node, owner: str) -> None:
        if fn.type != "member_access_expression":
            return
        method = _simple(fn.child_by_field_name("name"))
        receiver = fn.child_by_field_name("expression")
        receiver_name = _dotted(receiver) if receiver is not None else None
        if method == "GetEnvironmentVariable" and receiver_name in ("Environment", "System.Environment"):
            self._config_key(_first_arg(node), node, owner)
        elif method == "GetConnectionString":
            name = _first_arg(node)
            self._config_key(f"ConnectionStrings:{name}" if name else None, node, owner)
        elif method in _CONFIG_METHODS:
            key = _first_arg(node)
            prefix = self._section_prefix(receiver)
            self._config_key(f"{prefix}:{key}" if key and prefix else key, node, owner)

    def _is_configuration(self, node: Node | None) -> bool:
        if node is None:
            return False
        dotted = _dotted(node) or ""
        if dotted.rsplit(".", 1)[-1].lower().endswith("configuration"):
            return True
        declared = self._declared(_text(node)) if node.type == "identifier" else None
        return declared is not None and declared.endswith("Configuration")

    def _config_indexer(self, node: Node, owner: str) -> None:
        if not self._is_configuration(node.child_by_field_name("expression")):
            return
        subscript = node.child_by_field_name("subscript")
        first = subscript.named_children[0] if subscript is not None and subscript.named_children else None
        self._config_key(_string_value(first), node, owner)


class CSharpExtractor:
    language = "csharp"

    def extract(self, rel_path: str, source: bytes) -> ExtractResult:
        tree = _parser().parse(source)
        stem = rel_path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".cs")
        result = _Visitor(stem).run(tree.root_node)
        result.parse_error = tree.root_node.has_error
        return result
