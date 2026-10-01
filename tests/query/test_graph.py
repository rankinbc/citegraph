"""what_calls, what_does_it_call, find_path and explain_edge."""

from __future__ import annotations

from pathlib import Path

import pytest

from citegraph.models import ToolError
from citegraph.query.common import QueryContext
from citegraph.query.graph import explain_edge, find_path, what_calls, what_does_it_call
from citegraph.resolve.rules import CONFIDENCE
from tests.helpers import build_store

PO = "shop.orders.OrderService.place_order"


def test_what_calls_with_evidence(sample_ctx: QueryContext) -> None:
    answer = what_calls(sample_ctx, "charge")
    assert [(i.symbol.qualified_name, i.rule, i.confidence, i.path, i.line) for i in answer.data] == [
        (PO, "import_scope", 0.9, "src/shop/orders.py", 10)
    ]
    assert answer.source == "derived"
    assert answer.confidence == 0.9
    assert (answer.evidence[0].repo, answer.evidence[0].line) == ("shop", 10)


def test_what_calls_depth_two(sample_ctx: QueryContext) -> None:
    answer = what_calls(sample_ctx, "render_invoice", depth=2)
    assert [(i.symbol.qualified_name, i.depth) for i in answer.data] == [
        ("billing.invoices.create_invoice", 1),
        (PO, 2),
    ]


def test_what_does_it_call(sample_ctx: QueryContext) -> None:
    answer = what_does_it_call(sample_ctx, PO)
    assert {i.symbol.qualified_name for i in answer.data} == {
        "shop.orders.OrderService.validate",
        "shop.orders.compute_total",
        "shop.payments.charge",
        "billing.invoices.create_invoice",
        "billing.notifications.notify_customer",
    }
    assert answer.confidence == 0.6


def test_min_confidence_filters(sample_ctx: QueryContext) -> None:
    names = {i.symbol.qualified_name for i in what_does_it_call(sample_ctx, PO, min_confidence=0.8).data}
    assert "billing.notifications.notify_customer" not in names


def test_instantiation_is_a_caller(sample_ctx: QueryContext) -> None:
    items = what_calls(sample_ctx, "OrderService").data
    assert [(i.symbol.qualified_name, i.kind) for i in items] == [("shop.cli.main", "instantiate")]


def test_empty_result_has_note(sample_ctx: QueryContext) -> None:
    answer = what_calls(sample_ctx, "notify_customer", min_confidence=0.9)
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == CONFIDENCE["cross_repo_unique"]  # the hidden edge, not certainty
    assert any("no callers" in n for n in answer.notes)
    assert (
        "1 lower-confidence candidate hidden (rules: cross_repo_unique); pass min_confidence=0.6 to see them"
        in (answer.notes)
    )


@pytest.mark.parametrize("kwargs", [{"depth": 4}, {"depth": 0}, {"min_confidence": 1.5}, {"limit": 500}])
def test_invalid_ranges(sample_ctx: QueryContext, kwargs: dict[str, float]) -> None:
    with pytest.raises(ToolError) as info:
        what_calls(sample_ctx, "charge", **kwargs)  # type: ignore[arg-type]
    assert info.value.code == "invalid_argument"


def test_find_path(sample_ctx: QueryContext) -> None:
    answer = find_path(sample_ctx, "main", "render_invoice")
    assert [s.symbol.qualified_name for s in answer.data] == [
        "shop.cli.main",
        PO,
        "billing.invoices.create_invoice",
        "billing.invoices.render_invoice",
    ]
    assert answer.data[0].via is None
    assert answer.confidence == 0.7


def test_find_path_none(sample_ctx: QueryContext) -> None:
    answer = find_path(sample_ctx, "render_invoice", "main")
    assert answer.data == []
    assert any("no call path" in n for n in answer.notes)


def test_explain_edge(sample_ctx: QueryContext) -> None:
    answer = explain_edge(sample_ctx, PO, "charge")
    assert len(answer.data) == 1
    edge = answer.data[0]
    assert (edge.rule, edge.confidence, edge.curated, edge.line) == ("import_scope", 0.9, False, 10)
    assert "import" in edge.rule_meaning


def test_ambiguous_edges_are_noted(tmp_path: Path) -> None:
    repo = {
        "m1.py": "def handle_event():\n    pass\n",
        "m2.py": "def handle_event():\n    pass\n",
        "caller.py": "def go(bus):\n    bus.handle_event()\n",
    }
    ctx = QueryContext(build_store(tmp_path / "i.db", {"a": repo}), head_fn=lambda _p: "0" * 40)
    assert what_calls(ctx, "m1.handle_event").data == []  # calibrated below the default threshold
    answer = what_calls(ctx, "m1.handle_event", min_confidence=CONFIDENCE["ambiguous"])
    assert [i.rule for i in answer.data] == ["ambiguous"]
    assert any("1 of 2 candidates" in n for n in answer.notes)


AMBIGUOUS_ONLY = {
    "m1.py": "def handle_event():\n    pass\n",
    "m2.py": "def handle_event():\n    pass\n",
    "caller.py": "def go(bus):\n    bus.handle_event()\n",
}
HIDDEN_AMBIGUOUS = "lower-confidence {} hidden (rules: ambiguous); pass min_confidence=0.1 to see them"


@pytest.fixture
def ambiguous_ctx(tmp_path: Path) -> QueryContext:
    """A store whose only edges are ambiguous (0.15), all below the default min_confidence of 0.5."""
    return QueryContext(build_store(tmp_path / "i.db", {"a": AMBIGUOUS_ONLY}), head_fn=lambda _p: "0" * 40)


def test_hidden_callers_are_counted(ambiguous_ctx: QueryContext) -> None:
    answer = what_calls(ambiguous_ctx, "m1.handle_event")
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == CONFIDENCE["ambiguous"]
    assert "1 " + HIDDEN_AMBIGUOUS.format("candidate") in answer.notes


def test_hidden_callees_are_counted(ambiguous_ctx: QueryContext) -> None:
    answer = what_does_it_call(ambiguous_ctx, "caller.go")
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == CONFIDENCE["ambiguous"]
    assert "2 " + HIDDEN_AMBIGUOUS.format("candidates") in answer.notes


def test_no_hidden_note_once_the_threshold_admits_them(ambiguous_ctx: QueryContext) -> None:
    answer = what_does_it_call(ambiguous_ctx, "caller.go", min_confidence=0.1)
    assert len(answer.data) == 2
    assert not any("hidden" in n for n in answer.notes)


def test_symbol_without_any_edge_keeps_full_confidence(ambiguous_ctx: QueryContext) -> None:
    answer = what_calls(ambiguous_ctx, "caller.go")
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == 1.0
    assert not any("hidden" in n for n in answer.notes)


def test_find_path_counts_hidden_first_hops(ambiguous_ctx: QueryContext) -> None:
    answer = find_path(ambiguous_ctx, "caller.go", "m1.handle_event")
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == CONFIDENCE["ambiguous"]
    assert "2 " + HIDDEN_AMBIGUOUS.format("candidates") in answer.notes
    assert len(find_path(ambiguous_ctx, "caller.go", "m1.handle_event", min_confidence=0.1).data) == 2


def test_explain_edge_without_an_edge_is_derived(ambiguous_ctx: QueryContext) -> None:
    answer = explain_edge(ambiguous_ctx, "m1.handle_event", "m2.handle_event")
    assert answer.data == []
    assert answer.source == "derived"
    assert answer.confidence == 1.0
