from pathlib import Path

import pytest

from citegraph.models import ToolError
from citegraph.query.common import QueryContext
from citegraph.query.symbols import get_symbol, search_symbols
from citegraph.store import Store
from tests.helpers import build_store

PO = "shop.orders.OrderService.place_order"


def test_search_substring_with_evidence(sample_ctx: QueryContext) -> None:
    answer = search_symbols(sample_ctx, "order")
    names = [s.qualified_name for s in answer.data]
    assert PO in names
    assert answer.source == "parsed"
    assert answer.confidence == 1.0
    assert answer.stale is False
    assert {a.repo for a in answer.as_of} <= {"shop", "billing"}
    assert all(e.commit == "0" * 40 for e in answer.evidence)


def test_exact_name_ranks_first(sample_ctx: QueryContext) -> None:
    assert search_symbols(sample_ctx, "charge").data[0].qualified_name == "shop.payments.charge"


def test_short_query_uses_prefix(sample_ctx: QueryContext) -> None:
    assert "shop.cli.main" in [s.qualified_name for s in search_symbols(sample_ctx, "ma").data]


def test_filters(sample_ctx: QueryContext) -> None:
    answer = search_symbols(sample_ctx, "invoice", kind="function", repo="billing")
    assert {s.qualified_name for s in answer.data} == {
        "billing.invoices.create_invoice",
        "billing.invoices.render_invoice",
    }


def test_truncation_note(sample_ctx: QueryContext) -> None:
    answer = search_symbols(sample_ctx, "invoice", limit=1)
    assert len(answer.data) == 1
    assert any(n.startswith("truncated: 2 more") for n in answer.notes)


@pytest.mark.parametrize(("kwargs", "match"), [({"kind": "banana"}, "kind"), ({"limit": 0}, "limit")])
def test_invalid_arguments(sample_ctx: QueryContext, kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ToolError) as info:
        search_symbols(sample_ctx, "order", **kwargs)  # type: ignore[arg-type]
    assert info.value.code == "invalid_argument"
    assert match in info.value.message


def test_get_symbol_detail(sample_ctx: QueryContext) -> None:
    answer = get_symbol(sample_ctx, "OrderService")
    detail = answer.data
    assert detail.kind == "class"
    assert detail.container == "shop.orders"
    assert detail.children == {"method": ["place_order", "validate"]}
    assert (answer.evidence[0].path, answer.evidence[0].line) == ("src/shop/orders.py", 6)
    assert answer.notes == []


def test_get_symbol_children_truncation_note(tmp_path: Path) -> None:
    methods = "".join(f"    def m{i}(self):\n        pass\n\n" for i in range(55))
    store = build_store(tmp_path / "big.db", {"a": {"big.py": "class Big:\n" + methods}})
    ctx = QueryContext(store, head_fn=lambda _p: "0" * 40)
    answer = get_symbol(ctx, "Big")
    assert len(answer.data.children["method"]) == 50
    assert any(n.startswith("truncated:") for n in answer.notes)


def test_resolve_symbol_accepts_agent_forms(sample_ctx: QueryContext) -> None:
    for form in ["place_order", "place_order()", "OrderService.place_order", PO, f"shop:{PO}", f" {PO} "]:
        assert sample_ctx.resolve_symbol(form)["qualified_name"] == PO


def test_ambiguous_symbol_lists_candidates(tmp_path: Path) -> None:
    store = build_store(
        tmp_path / "i.db", {"a": {"x.py": "def dup():\n    pass\n"}, "b": {"y.py": "def dup():\n    pass\n"}}
    )
    ctx = QueryContext(store, head_fn=lambda _p: "0" * 40)
    with pytest.raises(ToolError) as info:
        ctx.resolve_symbol("dup")
    assert info.value.code == "ambiguous_symbol"
    assert {d["qualified_name"] for d in info.value.data} == {"x.dup", "y.dup"}
    assert ctx.resolve_symbol("a:x.dup")["repo"] == "a"


def test_ambiguous_more_than_fifty_candidates(tmp_path: Path) -> None:
    repos = {f"repo{i}": {"m.py": "def dup():\n    pass\n"} for i in range(51)}
    store = build_store(tmp_path / "many.db", repos)
    ctx = QueryContext(store, head_fn=lambda _p: "0" * 40)
    with pytest.raises(ToolError) as info:
        ctx.resolve_symbol("dup")
    assert info.value.code == "ambiguous_symbol"
    assert "more than 50 symbols match" in info.value.message
    assert len(info.value.data) == 50


def test_not_found_suggests_close_names(sample_ctx: QueryContext) -> None:
    with pytest.raises(ToolError) as info:
        sample_ctx.resolve_symbol("place_ordr")
    assert info.value.code == "not_found"
    assert PO in [d["qualified_name"] for d in info.value.data]


def test_stale_flag_and_note(sample_store: Store) -> None:
    ctx = QueryContext(sample_store, head_fn=lambda _p: "f" * 40)
    answer = search_symbols(ctx, "charge")
    assert answer.stale is True
    assert any("citegraph index" in n for n in answer.notes)


def test_unreadable_head_is_not_reported_as_behind(sample_store: Store) -> None:
    ctx = QueryContext(sample_store, head_fn=lambda _p: None)  # git missing, or the repo path is gone
    answer = search_symbols(ctx, "charge")
    assert answer.stale is True
    assert any("HEAD could not be read" in n for n in answer.notes)
    assert not any("behind HEAD" in n for n in answer.notes)
