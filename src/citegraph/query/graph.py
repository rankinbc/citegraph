"""Call-graph queries over resolved edges."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from citegraph.models import Answer, Evidence
from citegraph.query.common import (
    MAX_LIMIT,
    SYMBOL_SELECT,
    QueryContext,
    SymbolInfo,
    check_float,
    check_int,
    symbol_info,
)
from citegraph.resolve.rules import RULE_MEANING

CALL_KINDS = ("call", "instantiate")

EDGE_SELECT = """
SELECT e.id AS edge_id, e.from_symbol_id, e.to_symbol_id, e.kind AS edge_kind, e.rule, e.confidence,
       e.candidates, rf.line AS ref_line, ff.path AS ref_path, fr.name AS ref_repo, fr.head_sha AS ref_sha
FROM edges e
JOIN refs rf ON rf.id = e.ref_id
JOIN files ff ON ff.id = rf.file_id
JOIN repos fr ON fr.id = ff.repo_id
"""


class EdgeInfo(BaseModel):
    symbol: SymbolInfo
    depth: int
    kind: str
    rule: str
    confidence: float
    candidates: int
    path: str
    line: int


class PathStep(BaseModel):
    symbol: SymbolInfo
    via: EdgeInfo | None


class EdgeExplanation(BaseModel):
    kind: str
    rule: str
    rule_meaning: str
    confidence: float
    candidates: int
    curated: bool
    path: str
    line: int


def _edge_evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(repo=row["ref_repo"], path=row["ref_path"], line=row["ref_line"], commit=row["ref_sha"])


def _edge_info(row: sqlite3.Row, symbol: SymbolInfo, depth: int) -> EdgeInfo:
    return EdgeInfo(
        symbol=symbol,
        depth=depth,
        kind=row["edge_kind"],
        rule=row["rule"],
        confidence=row["confidence"],
        candidates=row["candidates"],
        path=row["ref_path"],
        line=row["ref_line"],
    )


def _neighbors(
    ctx: QueryContext, ids: list[int], direction: Literal["in", "out"], min_confidence: float
) -> list[sqlite3.Row]:
    column = "e.to_symbol_id" if direction == "in" else "e.from_symbol_id"
    id_marks = ",".join("?" * len(ids))
    kind_marks = ",".join("?" * len(CALL_KINDS))
    sql = (
        EDGE_SELECT
        + f" WHERE {column} IN ({id_marks}) AND e.confidence >= ? AND e.kind IN ({kind_marks})"
        + " ORDER BY e.confidence DESC, ff.path, rf.line"
    )
    return ctx.rows(sql, (*ids, min_confidence, *CALL_KINDS))


def _symbols_by_id(ctx: QueryContext, ids: Iterable[int]) -> dict[int, sqlite3.Row]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    marks = ",".join("?" * len(wanted))
    return {int(r["id"]): r for r in ctx.rows(SYMBOL_SELECT + f" WHERE s.id IN ({marks})", wanted)}


def _traverse(
    ctx: QueryContext,
    symbol: str,
    depth: int,
    min_confidence: float,
    limit: int,
    direction: Literal["in", "out"],
) -> Answer[list[EdgeInfo]]:
    check_int("depth", depth, 1, 3)
    check_float("min_confidence", min_confidence, 0.0, 1.0)
    check_int("limit", limit, 1, MAX_LIMIT)
    target = ctx.resolve_symbol(symbol)
    other_column = "from_symbol_id" if direction == "in" else "to_symbol_id"
    seen = {int(target["id"])}
    frontier = [int(target["id"])]
    items: list[EdgeInfo] = []
    evidence: list[Evidence] = []
    notes: list[str] = []
    for level in range(1, depth + 1):
        if not frontier:
            break
        rows = _neighbors(ctx, frontier, direction, min_confidence)
        symbols = _symbols_by_id(ctx, (int(r[other_column]) for r in rows))
        next_frontier: list[int] = []
        for row in rows:
            other = int(row[other_column])
            if other not in symbols:
                continue
            info = symbol_info(symbols[other])
            items.append(_edge_info(row, info, level))
            evidence.append(_edge_evidence(row))
            if row["rule"] == "ambiguous":
                notes.append(
                    f"{info.qualified_name}: 1 of {row['candidates']} candidates (matched by name only)"
                )
            if other not in seen:
                seen.add(other)
                next_frontier.append(other)
        frontier = next_frontier
    if len(items) > limit:
        notes.append(f"truncated: {len(items) - limit} more; lower depth or raise min_confidence")
        items, evidence = items[:limit], evidence[:limit]
    if not items:
        what = "callers" if direction == "in" else "callees"
        notes.append(f"no {what} at min_confidence >= {min_confidence}")
    return ctx.answer(
        items,
        evidence=evidence,
        sources=["derived"] if items else [],
        confidences=[i.confidence for i in items],
        repos={target["repo"], *(i.symbol.repo for i in items)},
        notes=notes,
    )


def what_calls(
    ctx: QueryContext, symbol: str, depth: int = 1, min_confidence: float = 0.5, limit: int = 25
) -> Answer[list[EdgeInfo]]:
    return _traverse(ctx, symbol, depth, min_confidence, limit, "in")


def what_does_it_call(
    ctx: QueryContext, symbol: str, depth: int = 1, min_confidence: float = 0.5, limit: int = 25
) -> Answer[list[EdgeInfo]]:
    return _traverse(ctx, symbol, depth, min_confidence, limit, "out")


def find_path(
    ctx: QueryContext, from_symbol: str, to_symbol: str, max_depth: int = 6, min_confidence: float = 0.5
) -> Answer[list[PathStep]]:
    check_int("max_depth", max_depth, 1, 10)
    check_float("min_confidence", min_confidence, 0.0, 1.0)
    start, goal = ctx.resolve_symbol(from_symbol), ctx.resolve_symbol(to_symbol)
    start_id, goal_id = int(start["id"]), int(goal["id"])
    parent: dict[int, tuple[int, sqlite3.Row] | None] = {start_id: None}
    frontier = [start_id]
    for _ in range(max_depth):
        if goal_id in parent or not frontier:
            break
        next_frontier: list[int] = []
        for row in _neighbors(ctx, frontier, "out", min_confidence):
            target = int(row["to_symbol_id"])
            if target not in parent:
                parent[target] = (int(row["from_symbol_id"]), row)
                next_frontier.append(target)
        frontier = next_frontier
    repos = {start["repo"], goal["repo"]}
    if goal_id not in parent:
        note = f"no call path within {max_depth} hops at min_confidence >= {min_confidence}"
        return ctx.answer([], evidence=[], sources=[], confidences=[], repos=repos, notes=[note])
    chain: list[tuple[int, sqlite3.Row]] = []
    node = goal_id
    while (link := parent[node]) is not None:
        chain.append((node, link[1]))
        node = link[0]
    chain.reverse()
    symbols = _symbols_by_id(ctx, [start_id, *(n for n, _ in chain)])
    steps = [PathStep(symbol=symbol_info(symbols[start_id]), via=None)]
    for depth, (node_id, row) in enumerate(chain, start=1):
        info = symbol_info(symbols[node_id])
        steps.append(PathStep(symbol=info, via=_edge_info(row, info, depth)))
    edges = [s.via for s in steps if s.via is not None]
    return ctx.answer(
        steps,
        evidence=[_edge_evidence(row) for _, row in chain],
        sources=["derived"] if edges else [],
        confidences=[e.confidence for e in edges],
        repos=repos | {s.symbol.repo for s in steps},
    )


def explain_edge(ctx: QueryContext, from_symbol: str, to_symbol: str) -> Answer[list[EdgeExplanation]]:
    source, target = ctx.resolve_symbol(from_symbol), ctx.resolve_symbol(to_symbol)
    rows = ctx.rows(
        EDGE_SELECT + " WHERE e.from_symbol_id = ? AND e.to_symbol_id = ? ORDER BY rf.line",
        (int(source["id"]), int(target["id"])),
    )
    explanations = [
        EdgeExplanation(
            kind=r["edge_kind"],
            rule=r["rule"],
            rule_meaning=RULE_MEANING[r["rule"]],
            confidence=r["confidence"],
            candidates=r["candidates"],
            curated=False,
            path=r["ref_path"],
            line=r["ref_line"],
        )
        for r in rows
    ]
    notes = [] if rows else [f"no edge from {source['qualified_name']} to {target['qualified_name']}"]
    return ctx.answer(
        explanations,
        evidence=[_edge_evidence(r) for r in rows],
        sources=["derived"] if rows else [],
        confidences=[e.confidence for e in explanations],
        repos={source["repo"], target["repo"]},
        notes=notes,
    )
