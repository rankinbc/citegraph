"""Shared query plumbing: symbol rows, name resolution, staleness, and the Answer builder."""

from __future__ import annotations

import difflib
import sqlite3
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from citegraph.models import Answer, AsOf, Evidence, Source, ToolError, combine_sources
from citegraph.store import Store

MAX_LIMIT = 200
SYMBOL_KINDS = frozenset({"module", "class", "interface", "function", "method"})

SYMBOL_SELECT = """
SELECT s.id, s.kind, s.name, s.qualified_name, s.line_start, s.line_end, s.visibility, s.param_count,
       f.path, r.name AS repo, r.head_sha, r.indexed_at, r.path AS repo_path
FROM symbols s JOIN files f ON f.id = s.file_id JOIN repos r ON r.id = f.repo_id
"""

HeadFn = Callable[[Path], str | None]


class SymbolInfo(BaseModel):
    repo: str
    qualified_name: str
    name: str
    kind: str
    path: str
    line_start: int
    line_end: int
    visibility: str
    param_count: int | None


def symbol_info(row: sqlite3.Row) -> SymbolInfo:
    return SymbolInfo(
        repo=row["repo"],
        qualified_name=row["qualified_name"],
        name=row["name"],
        kind=row["kind"],
        path=row["path"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        visibility=row["visibility"],
        param_count=row["param_count"],
    )


def evidence_for(row: sqlite3.Row) -> Evidence:
    return Evidence(repo=row["repo"], path=row["path"], line=row["line_start"], commit=row["head_sha"])


def like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def check_int(name: str, value: int, lo: int, hi: int) -> None:
    if not lo <= value <= hi:
        raise ToolError("invalid_argument", f"{name} must be between {lo} and {hi} (got {value})")


def check_float(name: str, value: float, lo: float, hi: float) -> None:
    if not lo <= value <= hi:
        raise ToolError("invalid_argument", f"{name} must be between {lo} and {hi} (got {value})")


def git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _candidate(row: sqlite3.Row) -> dict[str, object]:
    return {
        "qualified_name": row["qualified_name"],
        "repo": row["repo"],
        "path": row["path"],
        "line": row["line_start"],
    }


def _one_symbol(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    """The first row when the rows name one symbol: C# overloads and partial declarations share a qualified name
    and repo, and a file outside any namespace has a module symbol named like its type, which yields to it."""
    named = [r for r in rows if r["kind"] != "module"] or rows
    if not named or len({(r["repo"], r["qualified_name"], r["kind"]) for r in named}) != 1:
        return None
    return named[0]


class QueryContext:
    def __init__(self, store: Store, head_fn: HeadFn = git_head, stale_ttl_s: float = 60.0) -> None:
        self.store = store
        self._head_fn = head_fn
        self._ttl = stale_ttl_s
        self._heads: dict[str, tuple[float, str | None]] = {}

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def suggest(self, name: str) -> list[str]:
        prefix = like_escape(name[:3]) + "%"
        names = [
            r["name"]
            for r in self.rows(
                "SELECT DISTINCT name FROM symbols WHERE name LIKE ? ESCAPE '\\' LIMIT 2000", (prefix,)
            )
        ]
        return difflib.get_close_matches(name, names, n=5, cutoff=0.6)

    def resolve_symbol(self, name: str) -> sqlite3.Row:
        cleaned = name.strip().removesuffix("()")
        repo: str | None = None
        if ":" in cleaned:
            repo, cleaned = cleaned.split(":", 1)
        repo_clause = " AND r.name = ?" if repo else ""
        repo_params: tuple[object, ...] = (repo,) if repo else ()
        exact = self.rows(
            SYMBOL_SELECT + " WHERE s.qualified_name = ?" + repo_clause + " ORDER BY s.id",
            (cleaned, *repo_params),
        )
        if (one := _one_symbol(exact)) is not None:
            return one
        candidates = exact or self.rows(
            SYMBOL_SELECT
            + " WHERE (s.name = ? OR s.qualified_name LIKE ? ESCAPE '\\') AND s.kind != 'module'"
            + repo_clause
            + " ORDER BY s.qualified_name, s.id LIMIT 51",
            (cleaned, "%." + like_escape(cleaned), *repo_params),
        )
        if (one := _one_symbol(candidates)) is not None:
            return one
        if candidates:
            message = (
                f"more than 50 symbols match {name!r}"
                if len(candidates) > 50
                else f"{len(candidates)} symbols match {name!r}"
            )
            raise ToolError(
                "ambiguous_symbol",
                message,
                hint="pass a qualified name, or repo:qualified.name",
                data=[_candidate(r) for r in candidates[:50]],
            )
        suggestions = self.suggest(cleaned.rsplit(".", 1)[-1])
        data: list[dict[str, object]] = []
        for suggestion in suggestions:
            data.extend(
                _candidate(r) for r in self.rows(SYMBOL_SELECT + " WHERE s.name = ? LIMIT 5", (suggestion,))
            )
        raise ToolError("not_found", f"no symbol matches {name!r}", hint="try search_symbols", data=data)

    def symbol_ids(self, row: sqlite3.Row) -> list[int]:
        """Every declaration of the symbol `resolve_symbol` returned: same repo, qualified name and kind."""
        return [
            int(r["id"])
            for r in self.rows(
                "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id JOIN repos r ON r.id = f.repo_id "
                "WHERE s.qualified_name = ? AND s.kind = ? AND r.name = ? ORDER BY s.id",
                (row["qualified_name"], row["kind"], row["repo"]),
            )
        ]

    def current_head(self, repo_path: str) -> str | None:
        now = time.monotonic()
        cached = self._heads.get(repo_path)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        sha = self._head_fn(Path(repo_path))
        self._heads[repo_path] = (now, sha)
        return sha

    def answer[T](
        self,
        data: T,
        *,
        evidence: list[Evidence],
        sources: Iterable[Source],
        confidences: Iterable[float],
        repos: set[str],
        notes: list[str] | None = None,
    ) -> Answer[T]:
        repo_rows = [
            r
            for r in self.rows("SELECT name, path, head_sha, indexed_at FROM repos ORDER BY name")
            if r["name"] in repos
        ]
        heads = {r["name"]: self.current_head(r["path"]) for r in repo_rows}
        unreadable = [r["name"] for r in repo_rows if heads[r["name"]] is None]
        behind = [r["name"] for r in repo_rows if heads[r["name"]] not in (None, r["head_sha"])]
        stale = bool(unreadable or behind)
        all_notes = list(notes or [])
        if behind:
            all_notes.append("index is behind HEAD for at least one repo; run `citegraph index <root>`")
        if unreadable:
            all_notes.append(
                f"HEAD could not be read for {', '.join(unreadable)} (git is missing or the repo path is gone), "
                "so freshness is unknown and the answer is marked stale"
            )
        source_list = list(sources)
        return Answer(
            data=data,
            evidence=evidence,
            source=combine_sources(source_list),
            as_of=[
                AsOf(repo=r["name"], commit=r["head_sha"], indexed_at=datetime.fromisoformat(r["indexed_at"]))
                for r in repo_rows
            ],
            confidence=min(confidences, default=1.0),
            stale=stale,
            notes=all_notes,
        )
