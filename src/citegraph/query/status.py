"""status: what is indexed, how fresh it is, and how much of the graph resolved."""

from __future__ import annotations

from pydantic import BaseModel

from citegraph.models import Answer, ToolError
from citegraph.query.common import QueryContext


class RepoStatus(BaseModel):
    name: str
    indexed_sha: str
    current_sha: str | None
    stale: bool
    files: int
    symbols: int


class RunStatus(BaseModel):
    started: str
    finished: str | None
    parse_errors: int
    leak_scan_clean: bool | None


class StatusData(BaseModel):
    repos: list[RepoStatus]
    refs: int
    edges: int
    resolved_ratio: float
    last_run: RunStatus | None


def status(ctx: QueryContext) -> Answer[StatusData]:
    rows = ctx.rows(
        "SELECT r.name, r.path, r.head_sha, "
        "(SELECT count(*) FROM files f WHERE f.repo_id = r.id) AS files, "
        "(SELECT count(*) FROM symbols s JOIN files f ON f.id = s.file_id WHERE f.repo_id = r.id) AS symbols "
        "FROM repos r ORDER BY r.name"
    )
    if not rows:
        raise ToolError("not_indexed", "the index has no repositories", hint="run `citegraph index <root>`")
    repos: list[RepoStatus] = []
    for row in rows:
        current = ctx.current_head(row["path"])
        repos.append(
            RepoStatus(
                name=row["name"],
                indexed_sha=row["head_sha"],
                current_sha=current,
                stale=current != row["head_sha"],
                files=row["files"],
                symbols=row["symbols"],
            )
        )
    refs = int(ctx.conn.execute("SELECT count(*) FROM refs WHERE kind != 'import'").fetchone()[0])
    resolved = int(
        ctx.conn.execute(
            "SELECT count(DISTINCT e.ref_id) FROM edges e JOIN refs r ON r.id = e.ref_id WHERE r.kind != 'import'"
        ).fetchone()[0]
    )
    edges = int(ctx.conn.execute("SELECT count(*) FROM edges").fetchone()[0])
    run = ctx.rows(
        "SELECT started, finished, parse_errors, leak_scan_clean FROM index_runs ORDER BY id DESC LIMIT 1"
    )
    last_run = None
    if run:
        clean = run[0]["leak_scan_clean"]
        last_run = RunStatus(
            started=run[0]["started"],
            finished=run[0]["finished"],
            parse_errors=run[0]["parse_errors"],
            leak_scan_clean=None if clean is None else bool(clean),
        )
    data = StatusData(
        repos=repos,
        refs=refs,
        edges=edges,
        resolved_ratio=round(resolved / refs, 4) if refs else 1.0,
        last_run=last_run,
    )
    return ctx.answer(data, evidence=[], sources=["parsed"], confidences=[], repos={r.name for r in repos})
