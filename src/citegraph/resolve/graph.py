"""The in-memory symbol graph the resolver reads, and the lookups every language shares."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from citegraph.resolve.rules import MAX_CANDIDATES, is_stoplisted
from citegraph.store import Store


@dataclass(frozen=True)
class Sym:
    id: int
    file_id: int
    repo_id: int
    kind: str
    name: str
    qualified_name: str
    lang: str


class Resolution(NamedTuple):
    targets: list[Sym]
    rule: str


class RefRow(NamedTuple):
    id: int
    file_id: int
    from_qualified: str
    to_name: str
    kind: str
    receiver_type: str | None


@dataclass
class CsImports:
    """A C# file's using directives, by form."""

    namespaces: list[str] = field(default_factory=list[str])
    statics: list[str] = field(default_factory=list[str])
    aliases: dict[str, str] = field(default_factory=dict[str, str])

    def add(self, local_name: str, target: str) -> None:
        if local_name == "*":
            self.namespaces.append(target)
        elif local_name == "*static":
            self.statics.append(target)
        else:
            self.aliases[local_name] = target


@dataclass
class Graph:
    by_qname: dict[str, list[Sym]] = field(default_factory=lambda: defaultdict(list))
    by_name: dict[str, list[Sym]] = field(default_factory=lambda: defaultdict(list))
    file_repo: dict[int, int] = field(default_factory=dict[int, int])
    file_lang: dict[int, str] = field(default_factory=dict[int, str])
    file_module: dict[int, str] = field(default_factory=dict[int, str])
    file_syms: dict[int, dict[str, Sym]] = field(default_factory=lambda: defaultdict(dict))
    imports: dict[int, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    cs_imports: dict[int, CsImports] = field(default_factory=lambda: defaultdict(CsImports))
    cs_global_imports: dict[int, CsImports] = field(default_factory=lambda: defaultdict(CsImports))


def load_graph(store: Store) -> Graph:
    graph = Graph()
    for row in store.conn.execute("SELECT id, repo_id, lang, module FROM files"):
        graph.file_repo[int(row["id"])] = int(row["repo_id"])
        graph.file_lang[int(row["id"])] = str(row["lang"])
        graph.file_module[int(row["id"])] = str(row["module"] or "")
    sql = (
        "SELECT s.id, s.file_id, f.repo_id, f.lang, s.kind, s.name, s.qualified_name "
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
            row["lang"],
        )
        graph.by_qname[sym.qualified_name].append(sym)
        if sym.kind != "module":
            graph.by_name[sym.name].append(sym)
        graph.file_syms[sym.file_id][sym.qualified_name] = sym
    for row in store.conn.execute("SELECT file_id, local_name, target FROM imports"):
        file_id, local, target = int(row["file_id"]), str(row["local_name"]), str(row["target"])
        if graph.file_lang[file_id] != "csharp":
            graph.imports[file_id][local] = target
        elif local.startswith("*global"):
            rest = local.removeprefix("*global")
            form = "*" if not rest else "*static" if rest == "-static" else rest.removeprefix("=")
            graph.cs_global_imports[graph.file_repo[file_id]].add(form, target)
        else:
            graph.cs_imports[file_id].add(local, target)
    return graph


def lookup(
    graph: Graph, qualified: str, repo_id: int, lang: str, kinds: frozenset[str] | None = None
) -> Sym | None:
    """The symbol with this qualified name, preferring the referencing repo; `kinds` limits the symbol kinds."""
    candidates = [
        s for s in graph.by_qname.get(qualified, []) if s.lang == lang and (kinds is None or s.kind in kinds)
    ]
    same_repo = [s for s in candidates if s.repo_id == repo_id]
    if same_repo:
        return same_repo[0]
    return candidates[0] if len(candidates) == 1 else None


def by_name(
    graph: Graph, repo_id: int, name: str, lang: str, kinds: frozenset[str] | None = None
) -> Resolution | None:
    """The name rules; `kinds` limits candidates before the cap, as C# syntax tells a call from a `new`."""
    if not name or is_stoplisted(name, lang):
        return None
    candidates = [
        s for s in graph.by_name.get(name, []) if s.lang == lang and (kinds is None or s.kind in kinds)
    ]
    if not candidates or len(candidates) > MAX_CANDIDATES:
        return None
    same_repo = [s for s in candidates if s.repo_id == repo_id]
    if len(same_repo) == 1:
        return Resolution(same_repo, "repo_unique")
    if not same_repo and len(candidates) == 1:
        return Resolution(candidates, "cross_repo_unique")
    return Resolution(same_repo or candidates, "ambiguous")
