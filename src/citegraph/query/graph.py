"""Call-graph queries over resolved edges."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from citegraph.models import Answer, Evidence, Source
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
       e.candidates, rf.line AS ref_line, rf.note AS ref_note, ff.path AS ref_path, fr.name AS ref_repo,
       fr.head_sha AS ref_sha
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
    note: str | None
    path: str
    line: int


def _sources(rules: Iterable[str]) -> list[Source]:
    """Curated edges were declared by hand; every other edge was derived by the resolver."""
    return ["curated" if rule == "curated" else "derived" for rule in rules] or ["derived"]


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


def _hidden_edges(
    ctx: QueryContext, symbol_ids: list[int], direction: Literal["in", "out"], min_confidence: float
) -> list[sqlite3.Row]:
    """The symbol's own (level-1) call edges below min_confidence."""
    column = "to_symbol_id" if direction == "in" else "from_symbol_id"
    id_marks = ",".join("?" * len(symbol_ids))
    kind_marks = ",".join("?" * len(CALL_KINDS))
    return ctx.rows(
        f"SELECT rule, confidence FROM edges WHERE {column} IN ({id_marks}) AND confidence < ? "
        f"AND kind IN ({kind_marks})",
        (*symbol_ids, min_confidence, *CALL_KINDS),
    )


def _hidden_note(hidden: list[sqlite3.Row]) -> str:
    rules = ", ".join(sorted({str(r["rule"]) for r in hidden}))
    # one decimal, rounded down, so the suggested threshold admits every hidden edge
    lowest = math.floor(min(float(r["confidence"]) for r in hidden) * 10 + 1e-9) / 10
    noun = "candidate" if len(hidden) == 1 else "candidates"
    return (
        f"{len(hidden)} lower-confidence {noun} hidden (rules: {rules}); "
        f"pass min_confidence={lowest:g} to see them"
    )


def _empty_answer_confidences(hidden: list[sqlite3.Row]) -> list[float]:
    """An empty answer is as certain as its best hidden edge allows; 1.0 only with no edges at all."""
    return [max(float(r["confidence"]) for r in hidden)] if hidden else []


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
    """Call edges breadth-first from `symbol` up to `depth`, keeping those at or above min_confidence.

    The source is always "derived". The symbol's own call edges below min_confidence are counted in a note.
    Confidence is the lowest among the returned edges; for an empty answer it is the highest confidence among
    the hidden edges, so "no callers" never claims more certainty than the best hidden candidate allows, and
    1.0 only when the symbol has no call edges at all.
    """
    check_int("depth", depth, 1, 3)
    check_float("min_confidence", min_confidence, 0.0, 1.0)
    check_int("limit", limit, 1, MAX_LIMIT)
    target = ctx.resolve_symbol(symbol)
    target_ids = ctx.symbol_ids(target)
    other_column = "from_symbol_id" if direction == "in" else "to_symbol_id"
    seen = set(target_ids)
    frontier = list(target_ids)
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
    hidden = _hidden_edges(ctx, target_ids, direction, min_confidence)
    if hidden:
        notes.append(_hidden_note(hidden))
    return ctx.answer(
        items,
        evidence=evidence,
        sources=_sources(i.rule for i in items),
        confidences=[i.confidence for i in items] if items else _empty_answer_confidences(hidden),
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
    """Shortest call path at or above min_confidence. With no path, the start symbol's hidden call edges are
    counted in a note and set the confidence, by the same rule as `_traverse`."""
    check_int("max_depth", max_depth, 1, 10)
    check_float("min_confidence", min_confidence, 0.0, 1.0)
    start, goal = ctx.resolve_symbol(from_symbol), ctx.resolve_symbol(to_symbol)
    start_ids, goal_ids = ctx.symbol_ids(start), set(ctx.symbol_ids(goal))
    parent: dict[int, tuple[int, sqlite3.Row] | None] = dict.fromkeys(start_ids)
    frontier = list(start_ids)
    for _ in range(max_depth):
        if goal_ids & parent.keys() or not frontier:
            break
        next_frontier: list[int] = []
        for row in _neighbors(ctx, frontier, "out", min_confidence):
            target = int(row["to_symbol_id"])
            if target not in parent:
                parent[target] = (int(row["from_symbol_id"]), row)
                next_frontier.append(target)
        frontier = next_frontier
    repos = {start["repo"], goal["repo"]}
    reached = [g for g in sorted(goal_ids) if g in parent]
    if not reached:
        notes = [f"no call path within {max_depth} hops at min_confidence >= {min_confidence}"]
        hidden = _hidden_edges(ctx, start_ids, "out", min_confidence)
        if hidden:
            notes.append(_hidden_note(hidden))
        return ctx.answer(
            [],
            evidence=[],
            sources=["derived"],
            confidences=_empty_answer_confidences(hidden),
            repos=repos,
            notes=notes,
        )
    chain: list[tuple[int, sqlite3.Row]] = []
    node = reached[0]
    while (link := parent[node]) is not None:
        chain.append((node, link[1]))
        node = link[0]
    chain.reverse()
    symbols = _symbols_by_id(ctx, [node, *(n for n, _ in chain)])  # node is now the start declaration
    steps = [PathStep(symbol=symbol_info(symbols[node]), via=None)]
    for depth, (node_id, row) in enumerate(chain, start=1):
        info = symbol_info(symbols[node_id])
        steps.append(PathStep(symbol=info, via=_edge_info(row, info, depth)))
    edges = [s.via for s in steps if s.via is not None]
    return ctx.answer(
        steps,
        evidence=[_edge_evidence(row) for _, row in chain],
        sources=_sources(e.rule for e in edges),
        confidences=[e.confidence for e in edges],
        repos=repos | {s.symbol.repo for s in steps},
    )


def explain_edge(ctx: QueryContext, from_symbol: str, to_symbol: str) -> Answer[list[EdgeExplanation]]:
    source, target = ctx.resolve_symbol(from_symbol), ctx.resolve_symbol(to_symbol)
    source_ids, target_ids = ctx.symbol_ids(source), ctx.symbol_ids(target)
    source_marks, target_marks = ",".join("?" * len(source_ids)), ",".join("?" * len(target_ids))
    rows = ctx.rows(
        EDGE_SELECT
        + f" WHERE e.from_symbol_id IN ({source_marks}) AND e.to_symbol_id IN ({target_marks}) ORDER BY rf.line",
        (*source_ids, *target_ids),
    )
    explanations = [
        EdgeExplanation(
            kind=r["edge_kind"],
            rule=r["rule"],
            rule_meaning=RULE_MEANING[r["rule"]],
            confidence=r["confidence"],
            candidates=r["candidates"],
            curated=r["rule"] == "curated",
            note=r["ref_note"],
            path=r["ref_path"],
            line=r["ref_line"],
        )
        for r in rows
    ]
    notes = [] if rows else [f"no edge from {source['qualified_name']} to {target['qualified_name']}"]
    return ctx.answer(
        explanations,
        evidence=[_edge_evidence(r) for r in rows],
        sources=_sources(e.rule for e in explanations),
        confidences=[e.confidence for e in explanations],
        repos={source["repo"], target["repo"]},
        notes=notes,
    )
