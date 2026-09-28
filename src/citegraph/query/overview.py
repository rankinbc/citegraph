"""repo_overview: languages, top modules, entry points, fan-in hotspots."""

from __future__ import annotations

from pydantic import BaseModel

from citegraph.models import Answer, Evidence, ToolError
from citegraph.query.common import SYMBOL_SELECT, QueryContext, SymbolInfo, symbol_info


class ModuleCount(BaseModel):
    module: str
    symbols: int


class EntryPointInfo(BaseModel):
    kind: str
    name: str
    target: str | None
    path: str
    line: int


class Hotspot(BaseModel):
    symbol: SymbolInfo
    callers: int


class RepoOverview(BaseModel):
    repo: str
    head_sha: str
    languages: dict[str, dict[str, int]]
    top_modules: list[ModuleCount]
    entry_points: list[EntryPointInfo]
    hotspots: list[Hotspot]


def repo_overview(ctx: QueryContext, repo: str) -> Answer[RepoOverview]:
    found = ctx.rows("SELECT id, head_sha FROM repos WHERE name = ?", (repo,))
    if not found:
        names = [r["name"] for r in ctx.rows("SELECT name FROM repos ORDER BY name")]
        raise ToolError(
            "not_found", f"no indexed repo named {repo!r}", hint="indexed repos: " + ", ".join(names)
        )
    repo_id, head = int(found[0]["id"]), str(found[0]["head_sha"])
    languages = {
        r["lang"]: {"files": int(r["n"]), "loc": int(r["loc"])}
        for r in ctx.rows(
            "SELECT lang, count(*) AS n, sum(loc) AS loc FROM files WHERE repo_id = ? GROUP BY lang ORDER BY lang",
            (repo_id,),
        )
    }
    top_modules = [
        ModuleCount(module=r["module"], symbols=int(r["n"]))
        for r in ctx.rows(
            "SELECT f.module, count(s.id) AS n FROM files f JOIN symbols s ON s.file_id = f.id "
            "WHERE f.repo_id = ? AND f.module IS NOT NULL GROUP BY f.module ORDER BY n DESC, f.module LIMIT 10",
            (repo_id,),
        )
    ]
    entry_points = [
        EntryPointInfo(kind=r["kind"], name=r["name"], target=r["target"], path=r["path"], line=r["line"])
        for r in ctx.rows(
            "SELECT e.kind, e.name, e.target, e.line, f.path FROM entry_points e JOIN files f ON f.id = e.file_id "
            "WHERE f.repo_id = ? ORDER BY f.path, e.line",
            (repo_id,),
        )
    ]
    fan_in = ctx.rows(
        "SELECT e.to_symbol_id AS id, count(DISTINCT e.from_symbol_id) AS n FROM edges e "
        "JOIN symbols s ON s.id = e.to_symbol_id JOIN files f ON f.id = s.file_id "
        "WHERE f.repo_id = ? AND e.kind IN ('call', 'instantiate') AND e.confidence >= 0.5 "
        "GROUP BY e.to_symbol_id ORDER BY n DESC, e.to_symbol_id LIMIT 10",
        (repo_id,),
    )
    hotspots: list[Hotspot] = []
    for row in fan_in:
        symbol = ctx.rows(SYMBOL_SELECT + " WHERE s.id = ?", (row["id"],))[0]
        hotspots.append(Hotspot(symbol=symbol_info(symbol), callers=int(row["n"])))
    overview = RepoOverview(
        repo=repo,
        head_sha=head,
        languages=languages,
        top_modules=top_modules,
        entry_points=entry_points,
        hotspots=hotspots,
    )
    evidence = [Evidence(repo=repo, path=e.path, line=e.line, commit=head) for e in entry_points]
    return ctx.answer(
        overview,
        evidence=evidence,
        sources=["parsed", *(["derived"] if hotspots else [])],
        confidences=[],
        repos={repo},
    )
