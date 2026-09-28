"""find_config_key: where a config key is defined and where code reads it, across repos."""

from __future__ import annotations

from pydantic import BaseModel

from citegraph.keys import normalize_key
from citegraph.models import Answer, Evidence, ToolError
from citegraph.query.common import MAX_LIMIT, QueryContext, check_int, like_escape


class ConfigLocation(BaseModel):
    repo: str
    path: str
    line: int
    origin: str
    key_path: str
    reader: str | None


class ConfigKeyInfo(BaseModel):
    key: str
    definitions: list[ConfigLocation]
    reads: list[ConfigLocation]


def find_config_key(ctx: QueryContext, pattern: str, limit: int = 25) -> Answer[list[ConfigKeyInfo]]:
    check_int("limit", limit, 1, MAX_LIMIT)
    normalized = normalize_key(pattern)
    if not normalized:
        raise ToolError("invalid_argument", "pattern must not be empty")
    like = like_escape(normalized).replace("*", "%")
    if "%" not in like:
        like = f"%{like}%"
    rows = ctx.rows(
        "SELECT c.key_path, c.key_norm, c.line, c.origin, c.reader_qualified, f.path, r.name AS repo, r.head_sha "
        "FROM config_keys c JOIN files f ON f.id = c.file_id JOIN repos r ON r.id = f.repo_id "
        "WHERE c.key_norm LIKE ? ESCAPE '\\' ORDER BY c.key_norm, r.name, f.path, c.line",
        (like,),
    )
    groups: dict[str, ConfigKeyInfo] = {}
    commits: dict[tuple[str, str, int], str] = {}
    for row in rows:
        info = groups.setdefault(
            row["key_norm"], ConfigKeyInfo(key=row["key_norm"], definitions=[], reads=[])
        )
        location = ConfigLocation(
            repo=row["repo"],
            path=row["path"],
            line=row["line"],
            origin=row["origin"],
            key_path=row["key_path"],
            reader=row["reader_qualified"],
        )
        (info.reads if row["origin"] == "code-read" else info.definitions).append(location)
        commits[(row["repo"], row["path"], row["line"])] = row["head_sha"]
    keys = list(groups.values())
    notes = (
        [f"truncated: {len(keys) - limit} more keys; use a more specific pattern"]
        if len(keys) > limit
        else []
    )
    keys = keys[:limit]
    evidence = [
        Evidence(repo=loc.repo, path=loc.path, line=loc.line, commit=commits[(loc.repo, loc.path, loc.line)])
        for key in keys
        for loc in (*key.definitions, *key.reads)
    ]
    if not keys:
        notes.append(f"no config keys match {pattern!r}")
    return ctx.answer(
        keys,
        evidence=evidence,
        sources=["parsed"],
        confidences=[],
        repos={e.repo for e in evidence},
        notes=notes,
    )
