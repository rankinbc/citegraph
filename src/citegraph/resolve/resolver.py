"""Turn raw references into edges. Whole-graph pass; every edge records its rule and confidence."""

from __future__ import annotations

from collections import Counter

from citegraph.resolve.graph import Graph, RefRow, Resolution, Sym, by_name, load_graph, lookup
from citegraph.resolve.rules import CONFIDENCE
from citegraph.store import EdgeRow, Store


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
        hit = lookup(graph, ".".join([imported, *rest]), repo_id, "python")
        return Resolution([hit], "import_scope") if hit is not None else None
    return by_name(graph, repo_id, parts[-1], "python")


def _resolve_python(graph: Graph, ref: RefRow, source: Sym) -> Resolution | None:
    if ref.kind == "import":
        hit = lookup(graph, ref.to_name, graph.file_repo[ref.file_id], "python")
        return Resolution([hit], "import_scope") if hit is not None else None
    return _resolve_call(graph, ref.file_id, source, ref.to_name)


def resolve_all(store: Store) -> dict[str, int]:
    graph = load_graph(store)
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
    for ref in refs:
        stats["refs"] += 1
        source = graph.file_syms[ref.file_id].get(ref.from_qualified)
        if source is None:
            stats["unresolved"] += 1
            continue
        if graph.file_lang[ref.file_id] == "csharp":
            # until the C# rules land, a C# reference is resolved by the name rules alone
            resolution = by_name(
                graph, graph.file_repo[ref.file_id], ref.to_name.rsplit(".", 1)[-1], "csharp"
            )
        else:
            resolution = _resolve_python(graph, ref, source)
        if resolution is None:
            stats["unresolved"] += 1
            continue
        stats[resolution.rule] += 1
        for target in resolution.targets:
            kind = "instantiate" if ref.kind == "call" and target.kind == "class" else ref.kind
            rows.append(
                EdgeRow(
                    ref.id,
                    source.id,
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
