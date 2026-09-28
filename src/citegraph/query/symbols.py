"""search_symbols and get_symbol."""

from __future__ import annotations

from citegraph.models import Answer, ToolError
from citegraph.query.common import (
    MAX_LIMIT,
    SYMBOL_KINDS,
    SYMBOL_SELECT,
    QueryContext,
    SymbolInfo,
    check_int,
    evidence_for,
    fts_phrase,
    like_escape,
    symbol_info,
)


class SymbolDetail(SymbolInfo):
    container: str | None
    children: dict[str, list[str]]


def search_symbols(
    ctx: QueryContext, query: str, kind: str | None = None, repo: str | None = None, limit: int = 25
) -> Answer[list[SymbolInfo]]:
    check_int("limit", limit, 1, MAX_LIMIT)
    if kind is not None and kind not in SYMBOL_KINDS:
        raise ToolError("invalid_argument", f"kind must be one of {sorted(SYMBOL_KINDS)}")
    text = query.strip()
    if not text:
        raise ToolError("invalid_argument", "query must not be empty")
    filters = ""
    params: list[object] = []
    if kind:
        filters += " AND s.kind = ?"
        params.append(kind)
    if repo:
        filters += " AND r.name = ?"
        params.append(repo)
    if len(text) >= 3:
        base = (
            SYMBOL_SELECT
            + " JOIN symbols_fts ON symbols_fts.rowid = s.id WHERE symbols_fts MATCH ?"
            + filters
        )
        match: list[object] = [fts_phrase(text), *params]
    else:
        base = SYMBOL_SELECT + " WHERE s.name LIKE ? ESCAPE '\\'" + filters
        match = [like_escape(text) + "%", *params]
    total = int(ctx.conn.execute(f"SELECT count(*) FROM ({base})", match).fetchone()[0])
    rows = ctx.rows(
        base + " ORDER BY (s.name = ?) DESC, length(s.qualified_name), s.qualified_name LIMIT ?",
        [*match, text, limit],
    )
    notes = [f"truncated: {total - len(rows)} more; narrow with repo= or kind="] if total > len(rows) else []
    return ctx.answer(
        [symbol_info(r) for r in rows],
        evidence=[evidence_for(r) for r in rows],
        sources=["parsed"],
        confidences=[],
        repos={r["repo"] for r in rows},
        notes=notes,
    )


def get_symbol(ctx: QueryContext, name: str) -> Answer[SymbolDetail]:
    row = ctx.resolve_symbol(name)
    qualified: str = row["qualified_name"]
    children: dict[str, list[str]] = {}
    child_rows = ctx.rows(
        SYMBOL_SELECT
        + " WHERE s.qualified_name LIKE ? ESCAPE '\\' AND r.name = ? ORDER BY s.line_start LIMIT 500",
        (like_escape(qualified) + ".%", row["repo"]),
    )
    for child in child_rows:
        remainder: str = child["qualified_name"][len(qualified) + 1 :]
        if "." not in remainder:
            names = children.setdefault(child["kind"], [])
            if len(names) < 50:
                names.append(child["name"])
    detail = SymbolDetail(
        **symbol_info(row).model_dump(),
        container=qualified.rsplit(".", 1)[0] if "." in qualified else None,
        children=children,
    )
    return ctx.answer(
        detail, evidence=[evidence_for(row)], sources=["parsed"], confidences=[], repos={row["repo"]}
    )
