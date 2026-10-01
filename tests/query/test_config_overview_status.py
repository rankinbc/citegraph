from pathlib import Path

import pytest

from citegraph.models import ToolError
from citegraph.query import TOOLS
from citegraph.query.common import QueryContext
from citegraph.query.config import find_config_key
from citegraph.query.overview import repo_overview
from citegraph.query.status import status
from tests.helpers import build_store


def ctx_for(tmp_path: Path, files: dict[str, str]) -> QueryContext:
    return QueryContext(build_store(tmp_path / "i.db", {"big": files}), head_fn=lambda _p: "0" * 40)


def test_repo_overview_caps_entry_points(tmp_path: Path) -> None:
    main_block = 'def run():\n    pass\n\n\nif __name__ == "__main__":\n    run()\n'
    answer = repo_overview(ctx_for(tmp_path, {f"tools/t{i:02}.py": main_block for i in range(53)}), "big")
    assert len(answer.data.entry_points) == 50
    assert len(answer.evidence) == 50
    assert "truncated: 3 more entry points" in answer.notes


def test_find_config_key_caps_locations_per_key(tmp_path: Path) -> None:
    files = {
        f"src/m{i:02}.py": 'import os\n\n\ndef f():\n    return os.getenv("SHARED_KEY")\n' for i in range(55)
    }
    files |= {f"appsettings.e{i:02}.json": '{"SHARED_KEY": 1}\n' for i in range(53)}
    answer = find_config_key(ctx_for(tmp_path, files), "SHARED_KEY")
    [info] = answer.data
    assert (len(info.definitions), len(info.reads)) == (50, 50)
    assert len(answer.evidence) == 100
    assert "truncated: shared_key has 3 more definitions" in answer.notes
    assert "truncated: shared_key has 5 more reads" in answer.notes


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
