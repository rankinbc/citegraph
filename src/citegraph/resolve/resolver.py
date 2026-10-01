"""Turn raw references into edges. Whole-graph pass; every edge records its rule and confidence."""

from __future__ import annotations

from collections import Counter

from citegraph.resolve.csharp import CSharpResolver
from citegraph.resolve.graph import Graph, RefRow, Resolution, Sym, by_name, load_graph, lookup
from citegraph.resolve.queues import QUEUE_KINDS, QueueResolver, load_queue_index
from citegraph.resolve.rules import CONFIDENCE
from citegraph.store import EdgeRow, Store

MAX_REEXPORT_HOPS = 3


def _reexport(graph: Graph, qualified: str) -> str | None:
    """`pkg.name.rest` -> `<target>.rest` when module `pkg` imports `name` from `<target>`, deepest module first."""
    parts = qualified.split(".")
    for k in range(len(parts) - 1, 0, -1):
        for file_id in graph.module_files.get(".".join(parts[:k]), []):
            target = graph.imports[file_id].get(parts[k])
            if target is not None:
                return ".".join([target, *parts[k + 1 :]])
    return None


def _lookup_python(graph: Graph, qualified: str, repo_id: int) -> Sym | None:
    """A Python symbol by qualified name, following re-exports (`from .impl import work` in a package) up to
    MAX_REEXPORT_HOPS times; a cycle or a name no module defines resolves to nothing."""
    seen = {qualified}
    for _ in range(MAX_REEXPORT_HOPS + 1):
        hit = lookup(graph, qualified, repo_id, "python")
        if hit is not None:
            return hit
        following = _reexport(graph, qualified)
        if following is None or following in seen:
            return None
        seen.add(following)
        qualified = following
    return None


def _resolve_call(graph: Graph, file_id: int, source: Sym, to_name: str) -> Resolution | None:
    repo_id = graph.file_repo[file_id]
    parts = to_name.split(".")
    head, rest = parts[0], parts[1:]
    local = graph.file_syms[file_id]
    if head in ("self", "cls") and rest:
        if source.kind == "method" and len(rest) == 1:
            owner = source.qualified_name.rsplit(".", 1)[0]
            hit = local.get(f"{owner}.{rest[0]}")
            if hit is not None:
                return Resolution([hit], "same_file")
        return by_name(graph, repo_id, parts[-1], "python")
    if head == "?":
        return by_name(graph, repo_id, parts[-1], "python")
    for scope in (source.qualified_name, graph.file_module[file_id]):
        hit = local.get(".".join([scope, *parts]))
        if hit is not None:
            return Resolution([hit], "same_file")
    imported = graph.imports[file_id].get(head)
    if imported is not None:
        hit = _lookup_python(graph, ".".join([imported, *rest]), repo_id)
        return Resolution([hit], "import_scope") if hit is not None else None
    return by_name(graph, repo_id, parts[-1], "python")


def _resolve_python(graph: Graph, ref: RefRow, source: Sym) -> Resolution | None:
    if ref.kind == "import":
        hit = _lookup_python(graph, ref.to_name, graph.file_repo[ref.file_id])
        return Resolution([hit], "import_scope") if hit is not None else None
    return _resolve_call(graph, ref.file_id, source, ref.to_name)


def resolve_all(store: Store) -> dict[str, int]:
    graph = load_graph(store)
    csharp = CSharpResolver(graph)
    queues = QueueResolver(graph, load_queue_index(store, graph), csharp, _resolve_call)
    stats: Counter[str] = Counter()
    rows: list[EdgeRow] = []
    refs = [
        RefRow(
            int(r["id"]),
            int(r["file_id"]),
            r["from_qualified"],
            r["to_name"],
            r["kind"],
            r["receiver_type"],
        )
        for r in store.conn.execute(
            "SELECT id, file_id, from_qualified, to_name, kind, receiver_type FROM refs ORDER BY id"
        )
    ]
    # C# member lookups walk base types, so every C# inherit reference is resolved before the rest
    refs.sort(key=lambda r: not (graph.file_lang[r.file_id] == "csharp" and r.kind == "inherit"))
    for ref in refs:
        stats["refs"] += 1
        source = graph.file_syms[ref.file_id].get(ref.from_qualified)
        if source is None:
            stats["unresolved"] += 1
            continue
        from_id = source.id
        if graph.file_lang[ref.file_id] == "csharp":
            from_id = csharp.canonical(source).id
        if ref.kind in QUEUE_KINDS:
            resolution = queues.resolve(ref, source)
        elif graph.file_lang[ref.file_id] == "csharp":
            resolution = csharp.resolve(ref, source)
        else:
            resolution = _resolve_python(graph, ref, source)
        if resolution is None:
            stats["unresolved"] += 1
            continue
        stats[resolution.rule] += 1
        for target in resolution.targets:
            if ref.kind in QUEUE_KINDS:
                kind = "call"
            else:
                kind = "instantiate" if ref.kind == "call" and target.kind == "class" else ref.kind
            rows.append(
                EdgeRow(
                    ref.id,
                    from_id,
                    target.id,
                    kind,
                    resolution.rule,
                    CONFIDENCE[resolution.rule],
                    len(resolution.targets),
                )
            )
    stats["edges"] = store.replace_edges(rows)
    store.commit()
    return dict(stats)
