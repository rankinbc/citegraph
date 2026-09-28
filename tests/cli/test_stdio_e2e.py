"""Spawn `python -m citegraph serve` and talk MCP over stdio, as Claude Code would."""

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

import anyio
from mcp.client.stdio import stdio_client

from citegraph.indexer import index_root
from mcp import ClientSession, StdioServerParameters
from tests.fixtures.sample_corpus import BILLING, SHOP

MakeRepo = Callable[[str, dict[str, str]], Path]


def test_stdio_round_trip(make_repo: MakeRepo, repos_root: Path, citegraph_home: Path) -> None:
    make_repo("shop", SHOP)
    make_repo("billing", BILLING)
    index_root(repos_root)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "citegraph", "serve", "--root", str(repos_root)],
        env={**os.environ, "CITEGRAPH_HOME": str(citegraph_home)},
    )

    async def session_calls() -> tuple[list[str], dict[str, object]]:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("what_calls", {"symbol": "charge"})
            text = result.content[0].text  # type: ignore[union-attr]
            return [t.name for t in tools.tools], json.loads(text)

    names, payload = anyio.run(session_calls)
    assert len(names) == 9
    assert payload["data"][0]["symbol"]["qualified_name"] == "shop.orders.OrderService.place_order"  # type: ignore[index]
