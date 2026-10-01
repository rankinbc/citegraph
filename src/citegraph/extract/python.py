"""Python extractor: symbols, references, imports, config reads and entry points via tree-sitter."""

from __future__ import annotations

from typing import Literal

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from citegraph.extract.base import module_name_for, package_for
from citegraph.extract.names import is_name_shaped
from citegraph.models import ConfigKey, EntryPoint, ExtractResult, ImportFact, QueueHandler, Reference, Symbol

_LANGUAGE = Language(tspython.language())
_parser_instance: Parser | None = None

_ENV_GETTERS = frozenset({"os.getenv", "os.environ.get", "getenv", "environ.get"})
_ENV_MAPPINGS = frozenset({"os.environ", "environ"})
_PARAM_SKIP = frozenset({"comment", "keyword_separator", "positional_separator"})
_ACTOR_DECORATORS = frozenset({"dramatiq.actor", "actor"})
_SEND_METHODS = frozenset({"send", "send_with_options"})


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


def _dotted(node: Node) -> str | None:
    if node.type == "identifier":
        return _text(node)
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        base = _dotted(obj) if obj is not None else None
        if base is None or attr is None:
            return None
        return f"{base}.{_text(attr)}"
    return None


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    if any(c.type == "interpolation" for c in node.named_children):
        return None
    return "".join(_text(c) for c in node.named_children if c.type == "string_content")


def _visibility(name: str) -> Literal["public", "private"]:
    return "private" if name.startswith("_") else "public"


class _Visitor:
    def __init__(self, module: str, package: str) -> None:
        self.module = module
        self.package = package
        self.result = ExtractResult()

    def run(self, root: Node) -> ExtractResult:
        self.result.symbols.append(
            Symbol(
                kind="module",
                name=self.module.rsplit(".", 1)[-1],
                qualified_name=self.module,
                line_start=1,
                line_end=_end_line(root),
            )
        )
        self._walk(root, self.module, None)
        return self.result

    def _walk(self, node: Node, scope: str, class_q: str | None) -> None:
        for child in node.named_children:
            self._visit(child, scope, class_q)

    def _visit(self, node: Node, scope: str, class_q: str | None) -> None:
        kind = node.type
        if kind == "class_definition":
            self._class(node, scope)
        elif kind == "function_definition":
            self._function(node, scope, class_q)
        elif kind == "decorated_definition":
            for child in node.named_children:
                if child.type == "decorator":
                    self._walk(child, scope, class_q)
            definition = node.child_by_field_name("definition")
            if definition is not None:
                self._queue_handler(node, definition, scope)
                self._visit(definition, scope, class_q)
        elif kind in ("import_statement", "import_from_statement"):
            self._import(node)
        elif kind == "call":
            self._call(node, scope)
            self._walk(node, scope, class_q)
        elif kind == "subscript":
            self._env_subscript(node, scope)
            self._walk(node, scope, class_q)
        elif kind == "if_statement":
            self._main_guard(node, scope)
            self._walk(node, scope, class_q)
        else:
            self._walk(node, scope, class_q)

    def _queue_handler(self, node: Node, definition: Node, scope: str) -> None:
        """`@dramatiq.actor(actor_name="x")` or a bare `@actor`: the function handles jobs named x, or its own name."""
        if definition.type != "function_definition":
            return
        function = _text(definition.child_by_field_name("name"))
        for decorator in node.named_children:
            if decorator.type != "decorator" or not decorator.named_children:
                continue
            expression = decorator.named_children[0]
            called = expression.child_by_field_name("function") if expression.type == "call" else expression
            if called is None or _dotted(called) not in _ACTOR_DECORATORS:
                continue
            name = function
            args = expression.child_by_field_name("arguments") if expression.type == "call" else None
            for arg in args.named_children if args is not None else []:
                if arg.type == "keyword_argument" and _text(arg.child_by_field_name("name")) == "actor_name":
                    literal = _string_value(arg.child_by_field_name("value"))
                    name = literal if literal and is_name_shaped(literal) else name
            self.result.queue_handlers.append(
                QueueHandler(name=name, handler_qualified=f"{scope}.{function}", line=_line(node))
            )

    def _class(self, node: Node, scope: str) -> None:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{scope}.{name}"
        self.result.symbols.append(
            Symbol(
                kind="class",
                name=name,
                qualified_name=qualified,
                line_start=_line(node),
                line_end=_end_line(node),
                visibility=_visibility(name),
            )
        )
        supers = node.child_by_field_name("superclasses")
        if supers is not None:
            for arg in supers.named_children:
                base = _dotted(arg)
                if base is not None:
                    self.result.references.append(
                        Reference(from_qualified=qualified, to_name=base, kind="inherit", line=_line(arg))
                    )
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk(body, qualified, qualified)

    def _function(self, node: Node, scope: str, class_q: str | None) -> None:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return
        qualified = f"{scope}.{name}"
        is_method = class_q is not None and scope == class_q
        params = node.child_by_field_name("parameters")
        param_nodes = [c for c in params.named_children if c.type not in _PARAM_SKIP] if params else []
        count = len(param_nodes)
        if is_method and param_nodes and _text(param_nodes[0]) in ("self", "cls"):
            count -= 1
        self.result.symbols.append(
            Symbol(
                kind="method" if is_method else "function",
                name=name,
                qualified_name=qualified,
                line_start=_line(node),
                line_end=_end_line(node),
                visibility=_visibility(name),
                param_count=count,
            )
        )
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk(body, qualified, None)

    def _call(self, node: Node, scope: str) -> None:
        fn = node.child_by_field_name("function")
        if fn is None:
            return
        name = _dotted(fn)
        if name is None:
            attr = fn.child_by_field_name("attribute") if fn.type == "attribute" else None
            if attr is None:
                return
            name = f"?.{_text(attr)}"
        self.result.references.append(
            Reference(from_qualified=scope, to_name=name, kind="call", line=_line(node))
        )
        self._queue_send(node, fn, scope)
        if name in _ENV_GETTERS:
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            key = _string_value(first)
            if key:
                self.result.config_keys.append(
                    ConfigKey(key_path=key, line=_line(node), origin="code-read", reader_qualified=scope)
                )

    def _queue_send(self, node: Node, fn: Node, scope: str) -> None:
        """`<actor>.send(...)` (resolved later to the actor function) and `<x>.enqueue(Message(actor_name="x"))`."""
        if fn.type != "attribute":
            return
        method = _text(fn.child_by_field_name("attribute"))
        obj = fn.child_by_field_name("object")
        receiver = _dotted(obj) if obj is not None else None
        if method in _SEND_METHODS and receiver is not None:
            self.result.references.append(
                Reference(from_qualified=scope, to_name=receiver, kind="queue_actor", line=_line(node))
            )
            return
        if method != "enqueue":
            return
        args = node.child_by_field_name("arguments")
        message = args.named_children[0] if args is not None and args.named_children else None
        called = (
            message.child_by_field_name("function")
            if message is not None and message.type == "call"
            else None
        )
        dotted = _dotted(called) if called is not None else None
        if message is None or dotted is None or dotted.rsplit(".", 1)[-1] != "Message":
            return
        margs = message.child_by_field_name("arguments")
        for arg in margs.named_children if margs is not None else []:
            if arg.type == "keyword_argument" and _text(arg.child_by_field_name("name")) == "actor_name":
                job = _string_value(arg.child_by_field_name("value"))
                if job and is_name_shaped(job):
                    self.result.references.append(
                        Reference(from_qualified=scope, to_name=job, kind="queue", line=_line(node))
                    )

    def _env_subscript(self, node: Node, scope: str) -> None:
        value = node.child_by_field_name("value")
        if value is None or _dotted(value) not in _ENV_MAPPINGS:
            return
        key = _string_value(node.child_by_field_name("subscript"))
        if key:
            self.result.config_keys.append(
                ConfigKey(key_path=key, line=_line(node), origin="code-read", reader_qualified=scope)
            )

    def _main_guard(self, node: Node, scope: str) -> None:
        if scope != self.module:
            return
        condition = _text(node.child_by_field_name("condition")).replace(" ", "").replace("'", '"')
        if condition == '__name__=="__main__"':
            self.result.entry_points.append(EntryPoint(kind="main-block", name=self.module, line=_line(node)))

    def _add_import(self, local: str, target: str, line: int) -> None:
        self.result.imports.append(ImportFact(local_name=local, target=target, line=line))
        self.result.references.append(
            Reference(from_qualified=self.module, to_name=target, kind="import", line=line)
        )

    def _import(self, node: Node) -> None:
        line = _line(node)
        if node.type == "import_statement":
            for child in node.named_children:
                if child.type == "dotted_name":
                    head = _text(child).split(".")[0]
                    self._add_import(head, head, line)
                elif child.type == "aliased_import":
                    target = _text(child.child_by_field_name("name"))
                    self._add_import(_text(child.child_by_field_name("alias")), target, line)
            return
        base = self._from_base(node.child_by_field_name("module_name"))
        if base is None:
            return
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                name = _text(name_node.child_by_field_name("name"))
                local = _text(name_node.child_by_field_name("alias"))
            else:
                name = _text(name_node)
                local = name.split(".")[-1]
            self._add_import(local, f"{base}.{name}" if base else name, line)

    def _from_base(self, node: Node | None) -> str | None:
        if node is None:
            return None
        if node.type == "dotted_name":
            return _text(node)
        if node.type != "relative_import":
            return None
        prefix = next((c for c in node.children if c.type == "import_prefix"), None)
        dots = len(_text(prefix).strip())
        parts = self.package.split(".") if self.package else []
        drop = dots - 1  # "." is the current package; each extra dot goes up one level
        if drop > 0:
            parts = parts[: max(0, len(parts) - drop)]
        dotted = next((c for c in node.named_children if c.type == "dotted_name"), None)
        if dotted is not None:
            parts = [*parts, *_text(dotted).split(".")]
        return ".".join(parts)


class PythonExtractor:
    language = "python"

    def extract(self, rel_path: str, source: bytes) -> ExtractResult:
        tree = _parser().parse(source)
        result = _Visitor(module_name_for(rel_path), package_for(rel_path)).run(tree.root_node)
        result.parse_error = tree.root_node.has_error
        return result
