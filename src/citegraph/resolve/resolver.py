"""Turn raw references into edges. Whole-graph pass; every edge records its rule and confidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from citegraph.resolve.rules import CONFIDENCE, MAX_CANDIDATES, is_stoplisted
from citegraph.store import EdgeRow, Store


@dataclass(frozen=True)
class Sym:
    id: int
    file_id: int
    repo_id: int
    kind: str
    name: str
    qualified_name: str


class Resolution(NamedTuple):
    targets: list[Sym]
    rule: str


@dataclass
class _Graph:
    by_qname: dict[str, list[Sym]] = field(default_factory=lambda: defaultdict(list))
    by_name: dict[str, list[Sym]] = field(default_factory=lambda: defaultdict(list))
    file_repo: dict[int, int] = field(default_factory=dict[int, int])
    file_module: dict[int, str] = field(default_factory=dict[int, str])
    file_syms: dict[int, dict[str, Sym]] = field(default_factory=lambda: defaultdict(dict))
    imports: dict[int, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))


def _load(store: Store) -> _Graph:
    graph = _Graph()
    for row in store.conn.execute("SELECT id, repo_id, module FROM files"):
        graph.file_repo[int(row["id"])] = int(row["repo_id"])
        graph.file_module[int(row["id"])] = str(row["module"] or "")
    sql = (
        "SELECT s.id, s.file_id, f.repo_id, s.kind, s.name, s.qualified_name "
        "FROM symbols s JOIN files f ON f.id = s.file_id"
    )
    for row in store.conn.execute(sql):
        sym = Sym(
            int(row["id"]),
            int(row["file_id"]),
            int(row["repo_id"]),
            row["kind"],
            row["name"],
            row["qualified_name"],
        )
        graph.by_qname[sym.qualified_name].append(sym)
        if sym.kind != "module":
            graph.by_name[sym.name].append(sym)
        graph.file_syms[sym.file_id][sym.qualified_name] = sym
    for row in store.conn.execute("SELECT file_id, local_name, target FROM imports"):
        graph.imports[int(row["file_id"])][row["local_name"]] = row["target"]
    return graph


def _lookup(graph: _Graph, qualified: str, repo_id: int) -> Sym | None:
    candidates = graph.by_qname.get(qualified, [])
    same_repo = [s for s in candidates if s.repo_id == repo_id]
    if same_repo:
        return same_repo[0]
    return candidates[0] if len(candidates) == 1 else None


def _by_name(graph: _Graph, repo_id: int, name: str) -> Resolution | None:
    if not name or is_stoplisted(name):
        return None
    candidates = graph.by_name.get(name, [])
    if not candidates or len(candidates) > MAX_CANDIDATES:
        return None
    same_repo = [s for s in candidates if s.repo_id == repo_id]
    if len(same_repo) == 1:
        return Resolution(same_repo, "repo_unique")
    if not same_repo and len(candidates) == 1:
        return Resolution(candidates, "cross_repo_unique")
    return Resolution(same_repo or candidates, "ambiguous")


def _resolve_call(graph: _Graph, file_id: int, source: Sym, to_name: str) -> Resolution | None:
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
        return _by_name(graph, repo_id, parts[-1])
    if head == "?":
        return _by_name(graph, repo_id, parts[-1])
    for scope in (source.qualified_name, graph.file_module[file_id]):
        hit = local.get(".".join([scope, *parts]))
        if hit is not None:
            return Resolution([hit], "same_file")
    imported = graph.imports[file_id].get(head)
    if imported is not None:
        hit = _lookup(graph, ".".join([imported, *rest]), repo_id)
        return Resolution([hit], "import_scope") if hit is not None else None
    return _by_name(graph, repo_id, parts[-1])


def resolve_all(store: Store) -> dict[str, int]:
    graph = _load(store)
    stats: Counter[str] = Counter()
    rows: list[EdgeRow] = []
    for ref in store.conn.execute("SELECT id, file_id, from_qualified, to_name, kind FROM refs"):
        stats["refs"] += 1
        file_id = int(ref["file_id"])
        source = graph.file_syms[file_id].get(ref["from_qualified"])
        if source is None:
            stats["unresolved"] += 1
            continue
        if ref["kind"] == "import":
            hit = _lookup(graph, ref["to_name"], graph.file_repo[file_id])
            resolution = Resolution([hit], "import_scope") if hit is not None else None
        else:
            resolution = _resolve_call(graph, file_id, source, ref["to_name"])
        if resolution is None:
            stats["unresolved"] += 1
            continue
        stats[resolution.rule] += 1
        for target in resolution.targets:
            kind = "instantiate" if ref["kind"] == "call" and target.kind == "class" else ref["kind"]
            rows.append(
                EdgeRow(
                    int(ref["id"]),
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
