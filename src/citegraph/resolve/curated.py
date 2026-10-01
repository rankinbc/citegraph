"""Hand-written links: both ends resolved by exact qualified name, in any language and any repo."""

from __future__ import annotations

from citegraph.resolve.graph import Graph, RefRow, Sym


def find_symbol(graph: Graph, name: str) -> Sym | None:
    """`qualified.name` or `repo:qualified.name`: the one symbol, or the canonical (first) declaration of one
    group sharing that name, repo and kind (overloads, partial types). A module yields to a same-named type."""
    repo, _, qualified = name.rpartition(":") if ":" in name else ("", "", name)
    candidates = [
        s for s in graph.by_qname.get(qualified, []) if not repo or graph.repo_name[s.repo_id] == repo
    ]
    named = [s for s in candidates if s.kind != "module"] or candidates
    if not named or len({(s.repo_id, s.kind) for s in named}) != 1:
        return None
    return named[0]


def resolve_curated(graph: Graph, ref: RefRow) -> tuple[Sym, Sym] | None:
    source, target = find_symbol(graph, ref.from_qualified), find_symbol(graph, ref.to_name)
    return (source, target) if source is not None and target is not None else None
