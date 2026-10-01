"""C# resolution: declared receiver types, namespaces and using directives, then the shared name rules."""

from __future__ import annotations

from collections import defaultdict

from citegraph.resolve.graph import CsImports, Graph, RefRow, Resolution, Sym, by_name, lookup

TYPE_KINDS = frozenset({"class", "interface"})
MEMBER_KINDS = frozenset({"class", "interface", "method"})
METHOD_KINDS = frozenset({"method"})
MAX_BASE_HOPS = 3
LANG = "csharp"


def _namespace_chain(namespace: str) -> list[str]:
    """`A.B` -> [`A.B`, `A`, ``]: the current namespace, each parent, then the global namespace."""
    parts = namespace.split(".") if namespace else []
    return [".".join(parts[:i]) for i in range(len(parts), -1, -1)]


def _join(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _distinct(syms: list[Sym]) -> list[Sym]:
    seen: dict[str, Sym] = {}
    for sym in syms:
        seen.setdefault(sym.qualified_name, sym)
    return list(seen.values())


class CSharpResolver:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.bases: dict[str, list[str]] = defaultdict(list)

    # --- lookups ----------------------------------------------------------------------------------------

    def _type(self, qualified: str, repo_id: int) -> Sym | None:
        # kinds matter: a file outside any namespace names its module symbol after the file, often the type's name
        return lookup(self.graph, qualified, repo_id, LANG, TYPE_KINDS)

    def _imports(self, file_id: int) -> CsImports:
        local = self.graph.cs_imports.get(file_id, CsImports())
        shared = self.graph.cs_global_imports.get(self.graph.file_repo[file_id], CsImports())
        return CsImports(
            namespaces=[*local.namespaces, *shared.namespaces],
            statics=[*local.statics, *shared.statics],
            aliases={**shared.aliases, **local.aliases},
        )

    def _enclosing_type(self, source: Sym) -> str | None:
        if source.kind in TYPE_KINDS:
            return source.qualified_name
        if source.kind == "method":
            owner = source.qualified_name.rsplit(".", 1)[0]
            return owner if self._type(owner, source.repo_id) is not None else None
        return None

    def _outward(self, type_q: str | None, repo_id: int) -> list[str]:
        """The type and each type it is nested in, innermost first."""
        chain: list[str] = []
        while type_q and self._type(type_q, repo_id) is not None:
            chain.append(type_q)
            type_q = type_q.rsplit(".", 1)[0] if "." in type_q else None
        return chain

    def _namespace(self, source: Sym) -> str:
        if source.kind == "module":
            return self.graph.file_module[source.file_id]
        qualified = source.qualified_name
        local = self.graph.file_syms[source.file_id]
        while "." in qualified and qualified in local and local[qualified].kind != "module":
            qualified = qualified.rsplit(".", 1)[0]
        return "" if qualified in local and local[qualified].kind != "module" else qualified

    def find_member(self, type_q: str, member: str, repo_id: int) -> Sym | None:
        """`member` declared on the type, or on a base type or interface up to MAX_BASE_HOPS levels up."""
        frontier, seen = [type_q], set[str]()
        for _ in range(MAX_BASE_HOPS + 1):
            following: list[str] = []
            for current in frontier:
                if current in seen:
                    continue
                seen.add(current)
                hit = lookup(self.graph, f"{current}.{member}", repo_id, LANG, MEMBER_KINDS)
                if hit is not None:
                    return hit
                following.extend(self.bases.get(current, []))
            frontier = following
        return None

    def resolve_type(
        self, name: str, file_id: int, enclosing: str | None, namespace: str
    ) -> Resolution | None:
        repo_id = self.graph.file_repo[file_id]
        imports = self._imports(file_id)
        head, _, rest = name.partition(".")
        if head in imports.aliases:
            target = imports.aliases[head]
            hit = self._type(f"{target}.{rest}" if rest else target, repo_id)
            return Resolution([hit], "import_scope") if hit is not None else None
        for outer in self._outward(enclosing, repo_id):  # nested types of the enclosing types
            hit = self._type(f"{outer}.{name}", repo_id)
            if hit is not None:
                return Resolution([hit], "same_file" if hit.file_id == file_id else "same_namespace")
        for prefix in _namespace_chain(namespace):
            hit = self._type(_join(prefix, name), repo_id)
            if hit is not None:
                return Resolution([hit], "same_file" if hit.file_id == file_id else "same_namespace")
        hits = _distinct(
            [h for u in imports.namespaces if (h := self._type(f"{u}.{name}", repo_id)) is not None]
        )
        if not hits:
            return None
        return Resolution(hits, "import_scope" if len(hits) == 1 else "ambiguous")

    # --- references -------------------------------------------------------------------------------------

    def _member_of(self, types: Resolution, member: str, repo_id: int) -> Resolution | None:
        hits = _distinct(
            [
                h
                for t in types.targets
                if (h := self.find_member(t.qualified_name, member, repo_id)) is not None
            ]
        )
        if not hits:
            return None
        return Resolution(hits, types.rule if len(hits) == 1 else "ambiguous")

    def _by_name(self, ref: RefRow, repo_id: int, name: str) -> Resolution | None:
        """Step 5. A C# call never names a type (that takes `new`), and `new` or a base list only names one."""
        kinds = METHOD_KINDS if ref.kind == "call" else TYPE_KINDS
        return by_name(self.graph, repo_id, name, LANG, kinds)

    def _local_rule(self, hit: Sym, file_id: int) -> str:
        return "same_file" if hit.file_id == file_id else "declared_type"

    def resolve(self, ref: RefRow, source: Sym) -> Resolution | None:
        repo_id = self.graph.file_repo[ref.file_id]
        enclosing = self._enclosing_type(source)
        namespace = self._namespace(source)
        if ref.kind != "call":  # a base list entry or `new T()` names a type
            if ref.kind == "inherit":  # a base type is looked up from outside the type it is the base of
                enclosing = source.qualified_name.rsplit(".", 1)[0] if "." in source.qualified_name else None
            types = self.resolve_type(ref.to_name, ref.file_id, enclosing, namespace)
            if types is None:
                return self._by_name(ref, repo_id, ref.to_name.rsplit(".", 1)[-1])
            if ref.kind == "inherit":
                self.bases[source.qualified_name].extend(t.qualified_name for t in types.targets)
            return types
        if ref.receiver_type:  # step 2: x.Foo() where x has a declared type
            types = self.resolve_type(ref.receiver_type, ref.file_id, enclosing, namespace)
            if types is None:
                return self._by_name(ref, repo_id, ref.to_name)
            found = self._member_of(types, ref.to_name, repo_id)
            if found is None:  # the type is known and lacks the member: no guess by name
                return None
            return Resolution(found.targets, "declared_type" if len(found.targets) == 1 else "ambiguous")
        parts = ref.to_name.split(".")
        head, rest = parts[0], parts[1:]
        if head in ("this", "base") and len(rest) == 1:  # step 1
            starts = [] if enclosing is None else [enclosing] if head == "this" else self.bases[enclosing]
            for start in starts:
                hit = self.find_member(start, rest[0], repo_id)
                if hit is not None:
                    return Resolution([hit], self._local_rule(hit, ref.file_id))
            return self._by_name(ref, repo_id, rest[0])
        if head == "?":
            return self._by_name(ref, repo_id, parts[-1])
        if not rest:
            return self._plain(ref, enclosing, repo_id)
        # longest prefix that names a type: Type.Member, Ns.Type.Member, Alias.Member
        for i in range(len(parts) - 1, 0, -1):
            types = self.resolve_type(".".join(parts[:i]), ref.file_id, enclosing, namespace)
            if types is not None:
                if i == len(parts) - 1:
                    return self._member_of(types, parts[-1], repo_id)
                break
        return self._by_name(ref, repo_id, parts[-1])

    def _plain(self, ref: RefRow, enclosing: str | None, repo_id: int) -> Resolution | None:
        """Step 3 for `Foo()`: a member of the enclosing types, then of a `using static` type."""
        name = ref.to_name
        for outer in self._outward(enclosing, repo_id):
            hit = self.find_member(outer, name, repo_id)
            if hit is not None:
                return Resolution([hit], self._local_rule(hit, ref.file_id))
        imports = self._imports(ref.file_id)
        statics = _distinct(
            [
                h
                for s in imports.statics
                if (h := lookup(self.graph, f"{s}.{name}", repo_id, LANG, METHOD_KINDS)) is not None
            ]
        )
        if statics:
            return Resolution(statics, "import_scope" if len(statics) == 1 else "ambiguous")
        return self._by_name(ref, repo_id, name)
