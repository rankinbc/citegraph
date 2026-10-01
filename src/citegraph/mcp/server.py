"""Read-only stdio MCP server. Nine tools, every call audited, every response sanitized."""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from mcp.server.mcpserver import Context, MCPServer

from citegraph.audit import AuditLog
from citegraph.models import Answer, ToolError
from citegraph.query.common import HeadFn, QueryContext, git_head
from citegraph.query.config import find_config_key as q_find_config_key
from citegraph.query.graph import explain_edge as q_explain_edge
from citegraph.query.graph import find_path as q_find_path
from citegraph.query.graph import what_calls as q_what_calls
from citegraph.query.graph import what_does_it_call as q_what_does_it_call
from citegraph.query.overview import repo_overview as q_repo_overview
from citegraph.query.status import status as q_status
from citegraph.query.symbols import get_symbol as q_get_symbol
from citegraph.query.symbols import search_symbols as q_search_symbols
from citegraph.redact import sanitize_obj
from citegraph.store import Store

log = logging.getLogger("citegraph.mcp")

ALLOWED_TOOLS = frozenset(
    {
        "status",
        "search_symbols",
        "get_symbol",
        "what_calls",
        "what_does_it_call",
        "find_path",
        "find_config_key",
        "repo_overview",
        "explain_edge",
    }
)

INSTRUCTIONS = (
    "citegraph answers structural questions about the indexed repositories. Call status first. "
    "Every answer carries evidence (repo, path, line, commit), a source (parsed, derived, curated), "
    "a confidence between 0 and 1, and a stale flag. Cite the evidence. To read code, open the evidence "
    "path at the given line with your own file tools; citegraph never returns source text."
)


class ToolRunner:
    def __init__(self, db_path: Path, audit: AuditLog, head_fn: HeadFn = git_head) -> None:
        self._db_path = db_path
        self._audit = audit
        self._head_fn = head_fn
        self._ctx: QueryContext | None = None

    def context(self) -> QueryContext:
        if self._ctx is None:  # retried on every call until an index exists
            self._ctx = QueryContext(Store.open_read_only(self._db_path), head_fn=self._head_fn)
        return self._ctx

    def run(
        self, tool: str, args: dict[str, object], fn: Callable[[QueryContext], Answer[Any]], client: str
    ) -> dict[str, object]:
        started = time.perf_counter()
        error: str | None = None
        count = 0
        payload: dict[str, object]
        try:
            answer = fn(self.context())
            data = answer.data
            count = len(cast(list[object], data)) if isinstance(data, list) else 1
            payload = answer.model_dump(mode="json")
        except ToolError as exc:
            error = exc.code
            payload = exc.to_dict()
        except TypeError as exc:
            error = "invalid_argument"
            payload = {
                "error": "invalid_argument",
                "message": str(exc),
                "hint": "check the tool's arguments",
                "data": [],
            }
        except Exception:
            log.exception("tool %s failed", tool)
            error = "internal"
            payload = {
                "error": "internal",
                "message": "internal error; details are in the server log",
                "hint": "re-run `citegraph index <root>`; report a bug if it persists",
                "data": [],
            }
        finally:
            self._audit.record(
                tool=tool,
                args=args,
                result_count=count,
                duration_ms=(time.perf_counter() - started) * 1000,
                client=client,
                error=error,
            )
        return cast(dict[str, object], sanitize_obj(payload))


def _client(ctx: Any) -> str:
    try:
        return str(ctx.session.client_params.client_info.name)
    except Exception:
        pass
    try:
        return str(ctx.session.client_params.clientInfo.name)
    except Exception:
        return "unknown"


def build_server(db_path: Path, audit: AuditLog | None = None) -> MCPServer[Any]:
    mcp = MCPServer("citegraph", instructions=INSTRUCTIONS)
    runner = ToolRunner(db_path, audit or AuditLog())

    @mcp.tool()
    def status(ctx: Context[Any, Any]) -> dict[str, object]:
        """Index status: repos, indexed commit vs current HEAD (stale), counts, last run. Call this first."""
        return runner.run("status", {}, q_status, _client(ctx))

    @mcp.tool()
    def search_symbols(
        query: str, ctx: Context[Any, Any], kind: str | None = None, repo: str | None = None, limit: int = 25
    ) -> dict[str, object]:
        """Fuzzy search symbol names. kind: module|class|interface|function|method. Returns path:line evidence."""
        args: dict[str, object] = {"query": query, "kind": kind, "repo": repo, "limit": limit}
        return runner.run(
            "search_symbols", args, lambda q: q_search_symbols(q, query, kind, repo, limit), _client(ctx)
        )

    @mcp.tool()
    def get_symbol(name: str, ctx: Context[Any, Any]) -> dict[str, object]:
        """Look up one symbol (bare, qualified, or repo:qualified). Ambiguous names return candidates."""
        return runner.run("get_symbol", {"name": name}, lambda q: q_get_symbol(q, name), _client(ctx))

    @mcp.tool()
    def what_calls(
        symbol: str, ctx: Context[Any, Any], depth: int = 1, min_confidence: float = 0.5, limit: int = 25
    ) -> dict[str, object]:
        """Callers of a symbol (depth 1-3). Each edge has rule, confidence and the path:line of the call.
        Edges below min_confidence (default 0.5) are hidden and counted in notes; pass min_confidence=0.1 to
        see lower-confidence candidates such as name-only (ambiguous) matches."""
        args: dict[str, object] = {
            "symbol": symbol,
            "depth": depth,
            "min_confidence": min_confidence,
            "limit": limit,
        }
        return runner.run(
            "what_calls", args, lambda q: q_what_calls(q, symbol, depth, min_confidence, limit), _client(ctx)
        )

    @mcp.tool()
    def what_does_it_call(
        symbol: str, ctx: Context[Any, Any], depth: int = 1, min_confidence: float = 0.5, limit: int = 25
    ) -> dict[str, object]:
        """Callees of a symbol (depth 1-3). Each edge has rule, confidence and the path:line of the call.
        Edges below min_confidence (default 0.5) are hidden and counted in notes; pass min_confidence=0.1 to
        see lower-confidence candidates such as name-only (ambiguous) matches."""
        args: dict[str, object] = {
            "symbol": symbol,
            "depth": depth,
            "min_confidence": min_confidence,
            "limit": limit,
        }
        return runner.run(
            "what_does_it_call",
            args,
            lambda q: q_what_does_it_call(q, symbol, depth, min_confidence, limit),
            _client(ctx),
        )

    @mcp.tool()
    def find_path(
        from_symbol: str,
        to_symbol: str,
        ctx: Context[Any, Any],
        max_depth: int = 6,
        min_confidence: float = 0.5,
    ) -> dict[str, object]:
        """Shortest call path from one symbol to another, following edges at or above min_confidence."""
        args: dict[str, object] = {
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "max_depth": max_depth,
            "min_confidence": min_confidence,
        }
        return runner.run(
            "find_path",
            args,
            lambda q: q_find_path(q, from_symbol, to_symbol, max_depth, min_confidence),
            _client(ctx),
        )

    @mcp.tool()
    def find_config_key(pattern: str, ctx: Context[Any, Any], limit: int = 25) -> dict[str, object]:
        """Where config keys are defined (json/yaml/env example) and read in code. `*` is a wildcard.
        A:B, A__B and a.b spellings match each other. Values are never returned."""
        return runner.run(
            "find_config_key",
            {"pattern": pattern, "limit": limit},
            lambda q: q_find_config_key(q, pattern, limit),
            _client(ctx),
        )

    @mcp.tool()
    def repo_overview(repo: str, ctx: Context[Any, Any]) -> dict[str, object]:
        """Languages, top modules, entry points and fan-in hotspots for one indexed repo."""
        return runner.run("repo_overview", {"repo": repo}, lambda q: q_repo_overview(q, repo), _client(ctx))

    @mcp.tool()
    def explain_edge(from_symbol: str, to_symbol: str, ctx: Context[Any, Any]) -> dict[str, object]:
        """Why citegraph believes from_symbol calls to_symbol: rule, meaning, confidence, evidence."""
        return runner.run(
            "explain_edge",
            {"from_symbol": from_symbol, "to_symbol": to_symbol},
            lambda q: q_explain_edge(q, from_symbol, to_symbol),
            _client(ctx),
        )

    return mcp
