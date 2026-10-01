"""C# extractor: symbols, references with declared receiver types, using directives, config reads and entry points."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Node, Parser

from citegraph.extract import csharp_syntax as syn
from citegraph.extract.names import is_name_shaped
from citegraph.models import ConfigKey, EntryPoint, ExtractResult, ImportFact, Reference, StringConst, Symbol

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
_SECTION_METHODS = frozenset({"GetSection", "GetRequiredSection"})
DEFAULT_SEND_METHODS = ("Enqueue", "EnqueueAsync")
# a const string is kept only when its value looks like a name (a job name), never free text, paths or secrets
_CONFIG_METHODS = _SECTION_METHODS | {"GetValue"}
_NOT_CALLS = frozenset({"nameof"})


def _parser() -> Parser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = Parser(_LANGUAGE)
    return _parser_instance


class _Visitor:
    def __init__(self, stem: str, send_methods: Sequence[str]) -> None:
        self.stem = stem
        self.send_methods = frozenset(send_methods)
        self.result = ExtractResult()
        self.frames: list[dict[str, str]] = []
        self.interfaces: set[str] = set()
        self.top_level_line: int | None = None

    def run(self, root: Node) -> ExtractResult:
        namespace = syn.first_namespace(root)
        module = namespace or self.stem
        self.result.module = namespace
        self.result.symbols.append(
            Symbol(
                kind="module",
                name=module.rsplit(".", 1)[-1],
                qualified_name=module,
                line_start=1,
                line_end=syn.end_line(root),
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
        """Record a name in the innermost scope. Without a usable type it is recorded as "" (unknown), so it still
        shadows an outer declaration of the same name."""
        if name:
            self.frames[-1][name] = syn.type_name(type_node) or ""

    def _declared(self, name: str) -> str | None:
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name] or None
        return None

    def _declare_params(self, params: Node | None) -> None:
        if params is None:
            return
        if params.type in ("identifier", "implicit_parameter"):  # x => ...
            self._declare(syn.text(params), None)
            return
        for param in params.named_children:
            if param.type == "parameter":
                self._declare(syn.text(param.child_by_field_name("name")), param.child_by_field_name("type"))

    def _scoped(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        self.frames.append({})
        self._declare_params(node.child_by_field_name("parameters"))
        self._walk(node, namespace, owner, type_q)
        self.frames.pop()

    # --- traversal --------------------------------------------------------------------------------------

    def _walk(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        for child in node.named_children:
            if child.type == "file_scoped_namespace_declaration":  # its declarations follow as siblings
                namespace = syn.join(namespace, syn.type_name(child.child_by_field_name("name")) or "")
                continue
            self._visit(child, namespace, owner, type_q)

    def _visit(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        kind = node.type
        if kind == "namespace_declaration":
            inner = syn.join(namespace, syn.type_name(node.child_by_field_name("name")) or "")
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
                self.top_level_line = syn.line(node)
            self._walk(node, namespace, owner, type_q)
        elif kind == "using_directive":
            self._using(node)
        elif kind == "variable_declaration":
            self._variables(node, namespace, owner, type_q)
        elif kind in ("declaration_expression", "declaration_pattern"):
            self._declare(syn.text(node.child_by_field_name("name")), node.child_by_field_name("type"))
        elif kind == "foreach_statement":
            self._declare(syn.text(node.child_by_field_name("left")), node.child_by_field_name("type"))
            self._walk(node, namespace, owner, type_q)
        elif kind == "invocation_expression":
            self._invocation(node, owner)
            self._walk(node, namespace, owner, type_q)
        elif kind == "object_creation_expression":
            created = syn.type_name(node.child_by_field_name("type"))
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
        name = syn.text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{outer}.{name}" if outer else syn.join(namespace, name)
        kind = _TYPE_KINDS[node.type]
        if kind == "interface":
            self.interfaces.add(qualified)
        self.result.symbols.append(
            Symbol(
                kind=kind,
                name=name,
                qualified_name=qualified,
                line_start=syn.line(node),
                line_end=syn.end_line(node),
                visibility=syn.visibility(node, default_public=outer is None or outer in self.interfaces),
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
            self._string_consts(body, qualified)
            self._walk(body, namespace, qualified, qualified)
        self.frames.pop()

    def _bases(self, base_list: Node, qualified: str) -> None:
        for base in base_list.named_children:
            if base.type == "primary_constructor_base_type":
                base = base.child_by_field_name("type")
            name = syn.type_name(base)
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
                            self._declare(syn.text(declarator.child_by_field_name("name")), type_node)
            elif member.type == "property_declaration":
                self._declare(
                    syn.text(member.child_by_field_name("name")), member.child_by_field_name("type")
                )

    def _string_consts(self, body: Node, type_q: str) -> None:
        for member in body.named_children:
            if member.type != "field_declaration" or "const" not in syn.modifiers(member):
                continue
            decl = next((c for c in member.named_children if c.type == "variable_declaration"), None)
            for declarator in decl.named_children if decl is not None else []:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                value = syn.string_value(next((c for c in declarator.named_children if c != name_node), None))
                if value is not None and is_name_shaped(value):
                    self.result.string_consts.append(
                        StringConst(
                            qualified_name=f"{type_q}.{syn.text(name_node)}",
                            value=value,
                            line=syn.line(declarator),
                        )
                    )

    def _method(self, node: Node, namespace: str, type_q: str) -> None:
        mods = syn.modifiers(node)
        if node.type == "constructor_declaration":
            name = ".cctor" if "static" in mods else ".ctor"
        else:
            name = syn.text(node.child_by_field_name("name"))
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
                line_start=syn.line(node),
                line_end=syn.end_line(node),
                visibility=syn.visibility(node, default_public=type_q in self.interfaces),
                param_count=count,
            )
        )
        if name == "Main" and "static" in mods:
            self.result.entry_points.append(
                EntryPoint(kind="program-main", name=qualified, line=syn.line(node))
            )
        self._scoped(node, namespace, qualified, type_q)

    def _variables(self, node: Node, namespace: str, owner: str, type_q: str | None) -> None:
        type_node = node.child_by_field_name("type")
        declared = syn.type_name(type_node)
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value = next((c for c in declarator.named_children if c != name_node), None)
            if declared is None and value is not None and value.type == "object_creation_expression":
                self._declare(syn.text(name_node), value.child_by_field_name("type"))  # var x = new T()
            else:
                self._declare(syn.text(name_node), type_node)
            if value is None:
                continue
            if value.type == "implicit_object_creation_expression" and declared is not None:  # T x = new()
                self._ref(owner, declared, "instantiate", value)
            self._visit(value, namespace, owner, type_q)

    def _using(self, node: Node) -> None:
        tokens = {c.type for c in node.children if not c.is_named}
        alias = node.child_by_field_name("name")
        target_node = next((c for c in node.named_children if c != alias), None)
        target = syn.type_name(target_node)
        if not target:
            return
        if alias is not None:
            local = f"*global={syn.text(alias)}" if "global" in tokens else syn.text(alias)
        elif "static" in tokens:
            local = "*global-static" if "global" in tokens else "*static"
        else:
            local = "*global" if "global" in tokens else "*"
        self.result.imports.append(ImportFact(local_name=local, target=target, line=syn.line(node)))

    # --- references -------------------------------------------------------------------------------------

    def _ref(
        self,
        owner: str,
        to_name: str,
        kind: Literal["call", "inherit", "instantiate", "queue", "queue_const"],
        node: Node,
        receiver_type: str | None = None,
    ) -> None:
        self.result.references.append(
            Reference(
                from_qualified=owner,
                to_name=to_name,
                kind=kind,
                line=syn.line(node),
                receiver_type=receiver_type,
            )
        )

    def _member_target(self, expression: Node | None, name: str) -> tuple[str, str | None]:
        if expression is None:
            return f"?.{name}", None
        if expression.type in ("this", "base"):
            return f"{expression.type}.{name}", None
        if expression.type == "identifier":
            declared = self._declared(syn.text(expression))
            if declared is not None:
                return name, declared
        if expression.type == "member_access_expression":
            inner = expression.child_by_field_name("expression")
            if inner is not None and inner.type == "this":  # this._field.Foo()
                declared = self._declared(syn.simple(expression.child_by_field_name("name")) or "")
                return (name, declared) if declared is not None else (f"?.{name}", None)
        dotted = syn.dotted(expression)
        return (f"{dotted}.{name}", None) if dotted is not None else (f"?.{name}", None)

    def _call_target(self, fn: Node) -> tuple[str, str | None] | None:
        if fn.type in ("identifier", "generic_name"):
            name = syn.simple(fn)
            return (name, None) if name else None
        if fn.type == "member_access_expression":
            name = syn.simple(fn.child_by_field_name("name"))
            return self._member_target(fn.child_by_field_name("expression"), name) if name else None
        if fn.type == "conditional_access_expression":  # a?.Foo()
            binding = next((c for c in fn.named_children if c.type == "member_binding_expression"), None)
            name = syn.simple(binding.child_by_field_name("name")) if binding is not None else None
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
        if target[0].rsplit(".", 1)[-1] in self.send_methods:
            self._queue_send(node, owner)

    def _queue_send(self, node: Node, owner: str) -> None:
        """A job sent by name: a string literal, or a constant (`Tasks.Classify`) resolved later. A variable or
        parameter (a queue implementation forwarding `taskName`) is not a send."""
        args = node.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if first is None:
            return
        literal = syn.string_value(first)
        expression = first.named_children[-1] if first.named_children else None
        if literal and is_name_shaped(literal):
            self._ref(owner, literal, "queue", node)
        elif expression is not None and expression.type in ("member_access_expression", "qualified_name"):
            dotted = syn.dotted(expression)
            if dotted is not None:
                self._ref(owner, dotted, "queue_const", node)

    # --- config reads -----------------------------------------------------------------------------------

    def _config_key(self, key: str | None, node: Node, owner: str) -> None:
        if key:
            self.result.config_keys.append(
                ConfigKey(key_path=key, line=syn.line(node), origin="code-read", reader_qualified=owner)
            )

    def _section_prefix(self, node: Node | None) -> str | None:
        """`config.GetSection("A").GetSection("B")` -> `A:B` for the receiver of a chained config read."""
        if node is None or node.type != "invocation_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_access_expression":
            return None
        if syn.simple(fn.child_by_field_name("name")) not in _SECTION_METHODS:
            return None
        key = syn.first_arg(node)
        if not key:
            return None
        parent = self._section_prefix(fn.child_by_field_name("expression"))
        return f"{parent}:{key}" if parent else key

    def _config_call(self, node: Node, fn: Node, owner: str) -> None:
        if fn.type != "member_access_expression":
            return
        method = syn.simple(fn.child_by_field_name("name"))
        receiver = fn.child_by_field_name("expression")
        receiver_name = syn.dotted(receiver) if receiver is not None else None
        if method == "GetEnvironmentVariable" and receiver_name in ("Environment", "System.Environment"):
            self._config_key(syn.first_arg(node), node, owner)
        elif method == "GetConnectionString":
            name = syn.first_arg(node)
            self._config_key(f"ConnectionStrings:{name}" if name else None, node, owner)
        elif method in _CONFIG_METHODS:
            key = syn.first_arg(node)
            prefix = self._section_prefix(receiver)
            self._config_key(f"{prefix}:{key}" if key and prefix else key, node, owner)

    def _is_configuration(self, node: Node | None) -> bool:
        if node is None:
            return False
        dotted = syn.dotted(node) or ""
        if dotted.rsplit(".", 1)[-1].lower().endswith("configuration"):
            return True
        declared = self._declared(syn.text(node)) if node.type == "identifier" else None
        return declared is not None and declared.endswith("Configuration")

    def _config_indexer(self, node: Node, owner: str) -> None:
        if not self._is_configuration(node.child_by_field_name("expression")):
            return
        subscript = node.child_by_field_name("subscript")
        first = subscript.named_children[0] if subscript is not None and subscript.named_children else None
        self._config_key(syn.string_value(first), node, owner)


class CSharpExtractor:
    language = "csharp"

    def __init__(self, send_methods: Sequence[str] = DEFAULT_SEND_METHODS) -> None:
        self.send_methods = tuple(send_methods)

    def extract(self, rel_path: str, source: bytes) -> ExtractResult:
        tree = _parser().parse(source)
        stem = rel_path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".cs")
        result = _Visitor(stem, self.send_methods).run(tree.root_node)
        result.parse_error = tree.root_node.has_error
        return result
