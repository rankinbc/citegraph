"""Hand-written links in citegraph.overrides.yaml."""

from pathlib import Path

from citegraph.query.common import QueryContext
from citegraph.query.graph import explain_edge, what_calls
from citegraph.query.status import status
from citegraph.store import Store
from tests.helpers import build_store, edge_set

SHOP = {
    "orders/api.py": "def place_order():\n    pass\n",
    "payments/charge.py": "def charge():\n    pass\n",
}
MIRROR = {"payments/charge.py": "def charge():\n    pass\n"}  # the same qualified name in another repo

OVERRIDES = (
    "edges:\n"
    "  - from: orders.api.place_order\n"
    "    to: shop:payments.charge.charge\n"
    "    note: HTTP POST /charge via typed client\n"
    "  - from: orders.api.place_order\n"
    "    to: payments.missing.nothing\n"
    "  - from: orders.api.place_order\n"
    "    to: payments.charge.charge\n"
    "  - from: orders.api.place_order\n"
    "    to: charge\n"
)


def store_with(tmp_path: Path, overrides: str) -> Store:
    return build_store(
        tmp_path / "i.db", {"shop": {**SHOP, "citegraph.overrides.yaml": overrides}, "mirror": MIRROR}
    )


def test_a_curated_link_becomes_an_edge(tmp_path: Path) -> None:
    store = store_with(tmp_path, OVERRIDES)
    assert {e for e in edge_set(store) if e[3] == "curated"} == {
        ("orders.api.place_order", "payments.charge.charge", "call", "curated")
    }
    ctx = QueryContext(store, head_fn=lambda _path: "0" * 40)
    answer = what_calls(ctx, "shop:payments.charge.charge")
    assert [(i.symbol.qualified_name, i.rule, i.confidence, i.path, i.line) for i in answer.data] == [
        ("orders.api.place_order", "curated", 1.0, "citegraph.overrides.yaml", 2)
    ]
    assert answer.source == "curated"
    [why] = explain_edge(ctx, "orders.api.place_order", "shop:payments.charge.charge").data
    assert (why.curated, why.note, why.rule) == (True, "HTTP POST /charge via typed client", "curated")


def test_unknown_ambiguous_and_short_names_are_reported_by_status(tmp_path: Path) -> None:
    store = store_with(tmp_path, OVERRIDES)
    unresolved = status(QueryContext(store, head_fn=lambda _path: "0" * 40)).data.overrides_unresolved
    assert [(u.path, u.line, u.from_symbol, u.to_symbol) for u in unresolved] == [
        ("citegraph.overrides.yaml", 5, "orders.api.place_order", "payments.missing.nothing"),
        ("citegraph.overrides.yaml", 7, "orders.api.place_order", "payments.charge.charge"),  # in two repos
        ("citegraph.overrides.yaml", 9, "orders.api.place_order", "charge"),  # not a qualified name
    ]


def test_a_malformed_file_is_a_parse_error_with_no_edges(tmp_path: Path) -> None:
    for i, bad in enumerate(("edges: [", "edges:\n  - to: payments.charge.charge\n", "- just a list\n")):
        store = store_with(tmp_path / str(i), bad)
        row = store.conn.execute(
            "SELECT parse_error FROM files WHERE path = 'citegraph.overrides.yaml'"
        ).fetchone()
        assert row["parse_error"] == 1
        assert not any(e[3] == "curated" for e in edge_set(store))
