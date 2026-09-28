from pathlib import Path

from citegraph.resolve import resolve_all
from citegraph.resolve.rules import CONFIDENCE, is_stoplisted
from tests.fixtures.sample_corpus import SAMPLE, SAMPLE_EDGES
from tests.helpers import build_store, edge_set


def test_sample_corpus_edges(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", SAMPLE, resolve=False)
    stats = resolve_all(store)
    assert edge_set(store) == SAMPLE_EDGES
    assert stats["edges"] == len(SAMPLE_EDGES)
    assert stats["unresolved"] > 0  # builtins and os.* calls stay unresolved


def test_confidence_matches_rule(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", SAMPLE)
    for row in store.conn.execute("SELECT rule, confidence FROM edges"):
        assert row["confidence"] == CONFIDENCE[row["rule"]]


def test_ambiguous_creates_one_edge_per_candidate(tmp_path: Path) -> None:
    repo = {
        "m1.py": "def handle_event():\n    pass\n",
        "m2.py": "def handle_event():\n    pass\n",
        "caller.py": "def go(bus):\n    bus.handle_event()\n",
    }
    store = build_store(tmp_path / "i.db", {"a": repo})
    rows = store.conn.execute("SELECT rule, confidence, candidates FROM edges").fetchall()
    assert [(r["rule"], r["confidence"], r["candidates"]) for r in rows] == [
        ("ambiguous", CONFIDENCE["ambiguous"], 2)
    ] * 2


def test_stoplisted_names_stay_unresolved(tmp_path: Path) -> None:
    repo = {"m1.py": "def get():\n    pass\n", "caller.py": "def go(x):\n    x.get()\n    len(x)\n"}
    store = build_store(tmp_path / "i.db", {"a": repo})
    assert edge_set(store) == set()
    assert is_stoplisted("get") and is_stoplisted("len") and is_stoplisted("__init__")
    assert not is_stoplisted("place_order")


def test_too_many_candidates_stay_unresolved(tmp_path: Path) -> None:
    repo = {f"m{i}.py": "def on_tick():\n    pass\n" for i in range(11)}
    repo["caller.py"] = "def go(x):\n    x.on_tick()\n"
    store = build_store(tmp_path / "i.db", {"a": repo})
    assert edge_set(store) == set()


def test_external_import_is_not_guessed(tmp_path: Path) -> None:
    repo = {
        "m1.py": "def dumps(x):\n    pass\n",
        "caller.py": "import json\n\n\ndef go():\n    json.dumps(1)\n",
    }
    store = build_store(tmp_path / "i.db", {"a": repo})
    assert edge_set(store) == set()


def test_inheritance_edge(tmp_path: Path) -> None:
    repo = {
        "base.py": "class Base:\n    pass\n",
        "impl.py": "from base import Base\n\n\nclass Impl(Base):\n    pass\n",
    }
    store = build_store(tmp_path / "i.db", {"a": repo})
    assert ("impl.Impl", "base.Base", "inherit", "import_scope") in edge_set(store)


def test_resolve_is_idempotent(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", SAMPLE)
    resolve_all(store)
    assert edge_set(store) == SAMPLE_EDGES
