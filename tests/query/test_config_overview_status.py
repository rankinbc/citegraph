import pytest

from citegraph.models import ToolError
from citegraph.query import TOOLS
from citegraph.query.common import QueryContext
from citegraph.query.config import find_config_key
from citegraph.query.overview import repo_overview
from citegraph.query.status import status

TOOL_NAMES = {
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


def test_find_config_key_links_definitions_and_reads(sample_ctx: QueryContext) -> None:
    answer = find_config_key(sample_ctx, "TAX_RATE")
    assert [k.key for k in answer.data] == ["tax_rate"]
    key = answer.data[0]
    assert [(d.path, d.line, d.origin) for d in key.definitions] == [
        (".env.example", 2, "env-example"),
        ("appsettings.json", 6, "json"),
    ]
    assert [(r.path, r.line, r.reader) for r in key.reads] == [
        ("src/shop/orders.py", 20, "shop.orders.compute_total")
    ]
    assert len(answer.evidence) == 3
    assert answer.source == "parsed"


def test_find_config_key_substring_and_wildcard(sample_ctx: QueryContext) -> None:
    assert [k.key for k in find_config_key(sample_ctx, "payment").data] == [
        "payment:apiurl",
        "payment:timeoutseconds",
        "payment_api_url",
    ]
    assert [k.key for k in find_config_key(sample_ctx, "Payment:*").data] == [
        "payment:apiurl",
        "payment:timeoutseconds",
    ]


def test_find_config_key_normalizes_env_style(sample_ctx: QueryContext) -> None:
    assert [k.key for k in find_config_key(sample_ctx, "PAYMENT__APIURL").data] == ["payment:apiurl"]


def test_repo_overview(sample_ctx: QueryContext) -> None:
    overview = repo_overview(sample_ctx, "shop").data
    assert overview.languages["python"]["files"] == 4
    assert overview.languages["config"]["files"] == 3
    assert overview.top_modules[0].module == "shop.orders"
    assert [(e.kind, e.name, e.path, e.line) for e in overview.entry_points] == [
        ("console-script", "shop", "pyproject.toml", 5),
        ("main-block", "shop.cli", "src/shop/cli.py", 8),
    ]
    assert "shop.payments.charge" in {h.symbol.qualified_name for h in overview.hotspots}


def test_repo_overview_unknown_repo(sample_ctx: QueryContext) -> None:
    with pytest.raises(ToolError) as info:
        repo_overview(sample_ctx, "nope")
    assert info.value.code == "not_found"
    assert "billing" in info.value.hint and "shop" in info.value.hint


def test_status(sample_ctx: QueryContext) -> None:
    answer = status(sample_ctx)
    data = answer.data
    assert [r.name for r in data.repos] == ["billing", "shop"]
    assert not any(r.stale for r in data.repos)
    assert data.edges == 13
    assert 0 < data.resolved_ratio < 1
    assert data.last_run is None


def test_registry_has_exactly_the_spec_tools() -> None:
    assert set(TOOLS) == TOOL_NAMES
