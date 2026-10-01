"""Queue links: a job sent by name, matched to the function registered to handle that name, in any language."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from citegraph.resolve.csharp import CSharpResolver
from citegraph.resolve.graph import Graph, RefRow, Resolution, Sym
from citegraph.store import Store

QUEUE_KINDS = frozenset({"queue", "queue_const", "queue_actor"})

# resolves a Python call target the way the Python rules do: (graph, file_id, source, to_name)
PythonCall = Callable[[Graph, int, Sym, str], Resolution | None]


@dataclass
class QueueIndex:
    handlers: dict[str, list[Sym]] = field(default_factory=lambda: defaultdict(list))
    handler_ids: set[int] = field(default_factory=set[int])
    constants: dict[str, str] = field(default_factory=dict[str, str])


def load_queue_index(store: Store, graph: Graph) -> QueueIndex:
    index = QueueIndex()
    for row in store.conn.execute("SELECT file_id, name, handler_qualified FROM queue_handlers ORDER BY id"):
        sym = graph.file_syms[int(row["file_id"])].get(str(row["handler_qualified"]))
        if sym is not None:
            index.handlers[str(row["name"])].append(sym)
            index.handler_ids.add(sym.id)
    for row in store.conn.execute("SELECT qualified_name, value FROM string_consts ORDER BY id"):
        index.constants.setdefault(str(row["qualified_name"]), str(row["value"]))
    return index


class QueueResolver:
    def __init__(
        self, graph: Graph, index: QueueIndex, csharp: CSharpResolver, python_call: PythonCall
    ) -> None:
        self.graph = graph
        self.index = index
        self.csharp = csharp
        self.python_call = python_call

    def resolve(self, ref: RefRow, source: Sym) -> Resolution | None:
        if ref.kind == "queue_actor":  # Python `<actor>.send(...)`: the receiver is the handler itself
            target = self.python_call(self.graph, ref.file_id, source, ref.to_name)
            hits = [t for t in target.targets if t.id in self.index.handler_ids] if target is not None else []
            return Resolution(hits, "queue_match") if hits else None
        if ref.kind == "queue_const":
            name = self.csharp.constant_value(ref, source, self.index.constants)
        else:
            name = ref.to_name
        handlers = self.index.handlers.get(name or "", [])
        if not handlers:
            return None
        return Resolution(handlers, "queue_match" if len(handlers) == 1 else "ambiguous")
