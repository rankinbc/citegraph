import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from citegraph.audit import AuditLog
from citegraph.home import index_path
from citegraph.indexer import index_root
from citegraph.mcp.server import ALLOWED_TOOLS, ToolRunner, build_server
from citegraph.models import Answer, ToolError
from citegraph.query import TOOLS
from citegraph.query.common import QueryContext
from citegraph.query.graph import what_calls
from citegraph.query.status import status
from mcp import Client
from tests.fixtures.sample_corpus import BILLING, SAMPLE, SHOP
from tests.helpers import build_store, commit_all, load_secret_cases, write_files

SECRET = next(c for c in load_secret_cases() if c.kind == "github-token").value
MakeRepo = Callable[[str, dict[str, str]], Path]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sample.db"
    build_store(path, SAMPLE).close()
    return path


def runner_for(db: Path, tmp_path: Path) -> ToolRunner:
    return ToolRunner(db, AuditLog(tmp_path / "audit"), head_fn=lambda _p: "0" * 40)


def test_registered_tools_match_allowlist(db: Path, tmp_path: Path) -> None:
    server = build_server(db, AuditLog(tmp_path / "audit"))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == ALLOWED_TOOLS == set(TOOLS)


def test_connection_is_read_only(db: Path, tmp_path: Path) -> None:
    runner = runner_for(db, tmp_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        runner.context().conn.execute("DELETE FROM edges")


def test_run_returns_answer_and_audits(db: Path, tmp_path: Path) -> None:
    runner = runner_for(db, tmp_path)
    payload = runner.run("what_calls", {"symbol": "charge"}, what_calls, client="test")
    assert payload["source"] == "derived"
    audit_text = next((tmp_path / "audit").iterdir()).read_text(encoding="utf-8")
    assert '"tool": "what_calls"' in audit_text
    assert "place_order" not in audit_text  # result bodies are never logged


def test_egress_is_sanitized(db: Path, tmp_path: Path) -> None:
    runner = runner_for(db, tmp_path)

    def leaky(_ctx: QueryContext, x: str) -> Answer[list[str]]:
        return Answer(data=[x], notes=[x])

    payload = runner.run("status", {"x": SECRET}, leaky, client="test")
    assert SECRET not in repr(payload)
    assert SECRET not in next((tmp_path / "audit").iterdir()).read_text(encoding="utf-8")


def test_error_shapes(db: Path, tmp_path: Path) -> None:
    runner = runner_for(db, tmp_path)

    def boom(_ctx: QueryContext) -> Answer[None]:
        raise RuntimeError("secret internals")

    def broken(_ctx: QueryContext) -> Answer[None]:
        raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

    def ambiguous(_ctx: QueryContext) -> Answer[None]:
        raise ToolError("ambiguous_symbol", "2 symbols match", data=[{"qualified_name": "a.x"}])

    internal = runner.run("status", {}, boom, client="t")
    assert internal["error"] == "internal"
    assert "secret internals" not in repr(internal)
    bad = runner.run("what_calls", {"symbol": "charge", "bogus": 1}, what_calls, client="t")
    assert bad["error"] == "invalid_argument"
    assert "bogus" in str(bad["message"])
    assert runner.run("what_calls", {}, what_calls, client="t")["error"] == "invalid_argument"
    assert runner.run("status", {}, broken, client="t")["error"] == "internal"  # a TypeError inside the tool
    amb = runner.run("status", {}, ambiguous, client="t")
    assert amb == {
        "error": "ambiguous_symbol",
        "message": "2 symbols match",
        "hint": "",
        "data": [{"qualified_name": "a.x"}],
    }


def test_server_recovers_after_first_index(tmp_path: Path) -> None:
    path = tmp_path / "later.db"
    runner = runner_for(path, tmp_path)
    assert runner.run("status", {}, status, client="t")["error"] == "not_indexed"
    build_store(path, SAMPLE).close()
    assert "error" not in runner.run("status", {}, status, client="t")


def test_reads_work_during_reindex(make_repo: MakeRepo, repos_root: Path, tmp_path: Path) -> None:
    make_repo("shop", SHOP)
    billing = make_repo("billing", BILLING)
    index_root(repos_root)
    runner = ToolRunner(index_path(repos_root), AuditLog(tmp_path / "audit"))
    assert "error" not in runner.run("status", {}, status, client="t")
    write_files(billing, {"src/billing/extra.py": "def extra():\n    pass\n"})
    commit_all(billing, "add extra")
    index_root(repos_root)
    payload = runner.run("what_calls", {"symbol": "charge"}, what_calls, client="t")
    assert "error" not in payload


def test_client_name_is_recorded_via_mcp_client(db: Path, tmp_path: Path) -> None:
    """The client field on an audit line comes from the MCP session's clientInfo, when available."""
    audit_dir = tmp_path / "audit"
    server = build_server(db, AuditLog(audit_dir))

    async def _call() -> None:
        async with Client(server) as client:
            await client.call_tool("status", {})

    asyncio.run(_call())
    entries = AuditLog(audit_dir).entries()
    assert len(entries) == 1
    client_field = entries[0]["client"]
    assert isinstance(client_field, str) and client_field != ""
