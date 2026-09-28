"""Query functions. `TOOLS` maps each MCP tool name to its implementation."""

from collections.abc import Callable
from typing import Any

from citegraph.models import Answer
from citegraph.query.config import find_config_key
from citegraph.query.graph import explain_edge, find_path, what_calls, what_does_it_call
from citegraph.query.overview import repo_overview
from citegraph.query.status import status
from citegraph.query.symbols import get_symbol, search_symbols

TOOLS: dict[str, Callable[..., Answer[Any]]] = {
    "status": status,
    "search_symbols": search_symbols,
    "get_symbol": get_symbol,
    "what_calls": what_calls,
    "what_does_it_call": what_does_it_call,
    "find_path": find_path,
    "find_config_key": find_config_key,
    "repo_overview": repo_overview,
    "explain_edge": explain_edge,
}

__all__ = ["TOOLS"]
