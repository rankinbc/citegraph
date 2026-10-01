"""The query tools over a C# index: callers through interface-typed fields, config keys, overview."""

from __future__ import annotations

from pathlib import Path

import pytest

from citegraph.query.common import QueryContext
from citegraph.query.config import find_config_key
from citegraph.query.graph import explain_edge, find_path, what_calls, what_does_it_call
from citegraph.query.overview import repo_overview
from citegraph.resolve.rules import CONFIDENCE
from tests.fixtures.sample_corpus_csharp import CSHARP_SAMPLE, PLACE, POST
from tests.helpers import build_store


@pytest.fixture
def cs_ctx(tmp_path: Path) -> QueryContext:
    return QueryContext(build_store(tmp_path / "cs.db", CSHARP_SAMPLE), head_fn=lambda _path: "0" * 40)


def test_what_calls_an_interface_member(cs_ctx: QueryContext) -> None:
    answer = what_calls(cs_ctx, "Ordering.Domain.IOrderService.PlaceOrderAsync")
    assert [(i.symbol.qualified_name, i.rule, i.path, i.line) for i in answer.data] == [
        (POST, "declared_type", "src/Ordering.Api/OrdersController.cs", 21)
    ]
    assert answer.confidence == CONFIDENCE["declared_type"]


def test_cross_repo_call_through_a_using_directive(cs_ctx: QueryContext) -> None:
    answer = what_calls(cs_ctx, "Payments.PaymentClient.ChargeAsync")
    assert [(i.symbol.qualified_name, i.symbol.repo, i.rule) for i in answer.data] == [
        (PLACE, "ordering", "declared_type")
    ]


def test_find_path_from_controller_to_payments(cs_ctx: QueryContext) -> None:
    answer = find_path(cs_ctx, "Ordering.Domain.OrderService.PlaceOrderAsync", "SendAsync")
    assert [s.symbol.qualified_name for s in answer.data] == [
        PLACE,
        "Payments.PaymentClient.ChargeAsync",
        "Payments.PaymentClient.SendAsync",
    ]


def test_explain_edge_names_the_declared_type_rule(cs_ctx: QueryContext) -> None:
    answer = explain_edge(cs_ctx, POST, "Ordering.Domain.IOrderService.PlaceOrderAsync")
    assert [(e.rule, e.rule_meaning) for e in answer.data] == [
        (
            "declared_type",
            "the call is on a variable, field or parameter whose declared type has this member",
        )
    ]


def test_config_key_defined_in_appsettings_and_read_in_code(cs_ctx: QueryContext) -> None:
    answer = find_config_key(cs_ctx, "Payment:TimeoutSeconds")
    [info] = answer.data
    assert [(d.path, d.origin) for d in info.definitions] == [("src/Ordering.Api/appsettings.json", "json")]
    assert [(r.path, r.reader) for r in info.reads] == [("src/Ordering.Api/Program.cs", "Program")]


def test_repo_overview(cs_ctx: QueryContext) -> None:
    overview = repo_overview(cs_ctx, "ordering").data
    assert set(overview.languages) == {"csharp", "config"}
    assert [(m.module, m.symbols) for m in overview.top_modules] == [
        ("Ordering.Domain", 13),
        ("Ordering.Api", 4),
    ]
    assert [(e.kind, e.name, e.path) for e in overview.entry_points] == [
        ("program-main", "Program", "src/Ordering.Api/Program.cs")
    ]


GROUPS = {
    "Svc.cs": "namespace N;\nclass Svc\n{\n    public void Run() { }\n    public void Run(int x) { Log(); }\n    void Log() { }\n}\n",
    "P1.cs": "namespace N;\npartial class P { public void A() { } }\n",
    "P2.cs": "namespace N;\npartial class P { static readonly int X = Seed(); static int Seed() => 1; }\n",
    "K.cs": "class K { }\n",
    "Use.cs": "namespace N;\nclass U { void M(Svc s) { s.Run(); s.Run(1); new P(); new K(); } }\n",
}


def test_overloads_partial_classes_and_file_named_types_are_one_symbol(tmp_path: Path) -> None:
    # overloads and partial declarations share one qualified name; K.cs's module symbol is also named K
    ctx = QueryContext(build_store(tmp_path / "g.db", {"a": GROUPS}), head_fn=lambda _path: "0" * 40)
    assert [i.symbol.qualified_name for i in what_calls(ctx, "N.Svc.Run").data] == ["N.U.M", "N.U.M"]
    assert [i.symbol.qualified_name for i in what_does_it_call(ctx, "N.Svc.Run").data] == ["N.Svc.Log"]
    path = find_path(ctx, "N.U.M", "N.Svc.Log").data
    assert [s.symbol.qualified_name for s in path] == ["N.U.M", "N.Svc.Run", "N.Svc.Log"]
    assert [i.symbol.qualified_name for i in what_calls(ctx, "N.P").data] == ["N.U.M"]
    assert [i.symbol.qualified_name for i in what_does_it_call(ctx, "N.P").data] == ["N.P.Seed"]
    assert [i.symbol.qualified_name for i in what_calls(ctx, "K").data] == ["N.U.M"]
    assert [e.rule for e in explain_edge(ctx, "N.U.M", "N.Svc.Run").data] == [
        "declared_type",
        "declared_type",
    ]
