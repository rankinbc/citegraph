from collections.abc import Callable
from pathlib import Path

from citegraph.evaluation.baseline_grep import baseline_answer
from citegraph.evaluation.golden import GoldenQuestion
from citegraph.evaluation.spans import load_corpus_text
from tests.fixtures.sample_corpus import BILLING, SHOP

MakeRepo = Callable[[str, dict[str, str]], Path]

TRAP = {
    "m1.py": "def dispatch():\n    pass\n",
    "m2.py": "def dispatch():\n    pass\n",
    "a.py": "from m1 import dispatch\n\n\ndef x():\n    dispatch()\n",
    "b.py": "from m2 import dispatch\n\n\ndef y():\n    dispatch()\n",
}


def q(tool: str, **args: str) -> GoldenQuestion:
    return GoldenQuestion(id="t", tool=tool, args=args, expected=[])  # type: ignore[arg-type]


def test_enclosing_and_defs(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("shop", SHOP)
    text = load_corpus_text(repos_root)
    assert text.enclosing("shop", "src/shop/orders.py", 10) == "shop.orders.OrderService.place_order"
    assert text.enclosing("shop", "src/shop/orders.py", 1) == "shop.orders"
    assert [d.qualified for d in text.defs_by_name["charge"]] == ["shop.payments.charge"]


def test_callers_by_name_are_over_inclusive(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("t", TRAP)
    text = load_corpus_text(repos_root)
    assert sorted(baseline_answer(q("what_calls", symbol="t:m1.dispatch"), text) or []) == ["t:a.x", "t:b.y"]


def test_callees(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("shop", SHOP)
    make_repo("billing", BILLING)
    text = load_corpus_text(repos_root)
    got = baseline_answer(q("what_does_it_call", symbol="shop:shop.orders.OrderService.place_order"), text)
    assert set(got or []) == {
        "shop:shop.orders.OrderService.validate",
        "shop:shop.orders.compute_total",
        "shop:shop.payments.charge",
        "billing:billing.invoices.create_invoice",
        "billing:billing.notifications.notify_customer",
    }


def test_config_key_grep(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("shop", SHOP)
    text = load_corpus_text(repos_root)
    got = baseline_answer(q("find_config_key", pattern="TAX_RATE"), text)
    assert sorted(got or []) == [
        "shop:.env.example:2",
        "shop:appsettings.json:6",
        "shop:src/shop/orders.py:20",
    ]


MULTILINE_SIGNATURE = {
    "m.py": (
        "class Box:\n"
        "    def fill(\n"
        "        self,\n"
        "        item: str,\n"
        "    ) -> None:\n"  # closing line sits at the def's own indent
        "        helper(item)\n"
        "\n"
        "\n"
        "def helper(x):\n"
        "    return x\n"
    )
}

DOCSTRING_DEF = {
    "m.py": (
        "def outer():\n"
        '    """Example:\n'
        "\n"
        "def fake(a):\n"  # inside the docstring: must not open a scope
        "    pass\n"
        '"""\n'
        "    target()\n"
        "\n"
        "\n"
        "def target():\n"
        "    pass\n"
    )
}


def test_multiline_signature_keeps_the_body_in_the_function(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("r", MULTILINE_SIGNATURE)
    text = load_corpus_text(repos_root)
    assert text.enclosing("r", "m.py", 6) == "m.Box.fill"
    assert baseline_answer(q("what_does_it_call", symbol="r:m.Box.fill"), text) == ["r:m.helper"]
    assert baseline_answer(q("what_calls", symbol="r:m.helper"), text) == ["r:m.Box.fill"]


def test_def_inside_a_docstring_opens_no_scope(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("r", DOCSTRING_DEF)
    text = load_corpus_text(repos_root)
    assert "fake" not in text.defs_by_name
    assert text.enclosing("r", "m.py", 7) == "m.outer"
    assert baseline_answer(q("what_does_it_call", symbol="r:m.outer"), text) == ["r:m.target"]


def test_find_path_has_no_baseline(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("shop", SHOP)
    assert (
        baseline_answer(q("find_path", from_symbol="a", to_symbol="b"), load_corpus_text(repos_root)) is None
    )
