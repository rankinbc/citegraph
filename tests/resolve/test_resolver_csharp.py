from pathlib import Path

from citegraph.resolve.rules import CONFIDENCE, is_stoplisted
from tests.fixtures.sample_corpus import SAMPLE, SAMPLE_EDGES
from tests.fixtures.sample_corpus_csharp import CSHARP_SAMPLE, CSHARP_SAMPLE_EDGES
from tests.helpers import build_store, edge_set


def edges(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str, str]]:
    return edge_set(build_store(tmp_path / "i.db", {"a": files}))


def test_name_fallback_never_crosses_languages(tmp_path: Path) -> None:
    files = {
        "events.py": "def handle_event():\n    pass\n",
        "caller.py": "def go(bus):\n    bus.handle_event()\n",
        "Render.cs": "class Page { public void handle_event() { } }\n",
        "Caller.cs": "class C { void M() { x.Render(); } }\n",
        "render.py": "def Render():\n    pass\n",
    }
    assert edges(tmp_path, files) == {("caller.go", "events.handle_event", "call", "repo_unique")}


def test_csharp_stoplist_applies_to_csharp_only(tmp_path: Path) -> None:
    files = {
        "Bag.cs": "class Bag { public void Add() { } }\n",
        "Use.cs": "class U { void M() { items.Add(); } }\n",
        "bag.py": "def Add():\n    pass\n",
        "use.py": "def go(items):\n    items.Add()\n",
    }
    assert edges(tmp_path, files) == {("use.go", "bag.Add", "call", "repo_unique")}
    assert is_stoplisted("Add", "csharp") and not is_stoplisted("Add", "python")


def test_sample_corpus_edges(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", CSHARP_SAMPLE)
    assert edge_set(store) == CSHARP_SAMPLE_EDGES
    for row in store.conn.execute("SELECT rule, confidence FROM edges"):
        assert row["confidence"] == CONFIDENCE[row["rule"]]


def test_python_edges_are_unchanged_next_to_csharp(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", {**SAMPLE, **CSHARP_SAMPLE})
    assert edge_set(store) == SAMPLE_EDGES | CSHARP_SAMPLE_EDGES


def test_this_call_on_a_partial_class_in_another_file(tmp_path: Path) -> None:
    files = {
        "A.cs": "namespace N;\npartial class P { void A() { this.B(); B(); } }\n",
        "B.cs": "namespace N;\npartial class P { void B() { } }\n",
    }
    assert edges(tmp_path, files) == {
        ("N.P.A", "N.P.B", "call", "declared_type"),
    }


def test_base_call_reaches_the_base_type(tmp_path: Path) -> None:
    files = {
        "K.cs": "class Base { public void Save() { } }\nclass K : Base { void Save() { base.Save(); } }\n"
    }
    assert ("K.Save", "Base.Save", "call", "same_file") in edges(tmp_path, files)


def test_receiver_member_found_through_base_types(tmp_path: Path) -> None:
    files = {
        "Types.cs": (
            "namespace N;\n"
            "interface IRoot { void Ping(); }\n"
            "interface IMid : IRoot { }\n"
            "class Impl : IMid { }\n"
        ),
        "Use.cs": "namespace N;\nclass U { void M(Impl impl) { impl.Ping(); } }\n",
    }
    assert ("N.U.M", "N.IRoot.Ping", "call", "declared_type") in edges(tmp_path, files)


def test_base_type_walk_stops_after_three_hops(tmp_path: Path) -> None:
    files = {
        "Types.cs": (
            "namespace N;\n"
            "class L0 { public void Ping() { } }\n"
            "class L1 : L0 { }\nclass L2 : L1 { }\nclass L3 : L2 { }\nclass L4 : L3 { }\n"
        ),
        "Use.cs": "namespace N;\nclass U { void A(L3 x) { x.Ping(); } void B(L4 y) { y.Ping(); } }\n",
    }
    calls = {(f, t) for f, t, kind, _ in edges(tmp_path, files) if kind == "call"}
    assert calls == {("N.U.A", "N.L0.Ping")}


def test_circular_inheritance_terminates(tmp_path: Path) -> None:
    files = {"K.cs": "class A : B { }\nclass B : A { }\nclass U { void M(A a) { a.Missing(); } }\n"}
    assert {kind for _, _, kind, _ in edges(tmp_path, files)} == {"inherit"}


def test_known_receiver_type_without_the_member_is_not_guessed(tmp_path: Path) -> None:
    files = {
        "Repo.cs": "namespace N;\nclass Repo { }\n",
        "Other.cs": "namespace N;\nclass Other { public void Persist() { } }\n",
        "Use.cs": "namespace N;\nclass U { void M(Repo r) { r.Persist(); } }\n",
    }
    assert edges(tmp_path, files) == set()


def test_unknown_receiver_type_falls_back_to_name_rules(tmp_path: Path) -> None:
    files = {
        "Other.cs": "namespace N;\nclass Other { public void Persist() { } }\n",
        "Use.cs": "namespace N;\nclass U { void M(ILogger log) { log.Persist(); } }\n",
    }
    assert edges(tmp_path, files) == {("N.U.M", "N.Other.Persist", "call", "repo_unique")}


def test_parent_namespace_is_same_namespace(tmp_path: Path) -> None:
    files = {
        "Clock.cs": "namespace A;\npublic static class Clock { public static int Now() => 1; }\n",
        "Use.cs": "namespace A.B.C;\nclass U { void M() { Clock.Now(); new Clock(); } }\n",
    }
    assert edges(tmp_path, files) == {
        ("A.B.C.U.M", "A.Clock.Now", "call", "same_namespace"),
        ("A.B.C.U.M", "A.Clock", "instantiate", "same_namespace"),
    }


def test_alias_static_and_global_usings(tmp_path: Path) -> None:
    files = {
        "Lib.cs": (
            "namespace Lib.Util;\n"
            "public static class MathX { public static int Twice(int x) => x * 2; }\n"
            "public class Clock { public static int Now() => 1; }\n"
        ),
        "Globals.cs": "global using Lib.Util;\n",
        "Use.cs": (
            "using M = Lib.Util.MathX;\n"
            "using static Lib.Util.MathX;\n"
            "namespace App;\n"
            "class U { void A() { M.Twice(1); Twice(2); Clock.Now(); } }\n"
        ),
    }
    assert edges(tmp_path, files) == {
        ("App.U.A", "Lib.Util.MathX.Twice", "call", "import_scope"),
        ("App.U.A", "Lib.Util.Clock.Now", "call", "import_scope"),
    }


def test_same_type_name_in_two_imported_namespaces_is_ambiguous(tmp_path: Path) -> None:
    files = {
        "X.cs": "namespace X;\npublic class Order { public void Ship() { } }\n",
        "Y.cs": "namespace Y;\npublic class Order { public void Ship() { } }\n",
        "Use.cs": "using X;\nusing Y;\nnamespace App;\nclass U { void M(Order o) { o.Ship(); } }\n",
    }
    store = build_store(tmp_path / "i.db", {"a": files})
    rows = store.conn.execute("SELECT rule, candidates FROM edges").fetchall()
    assert [(r["rule"], r["candidates"]) for r in rows] == [("ambiguous", 2), ("ambiguous", 2)]


def test_nested_type_by_simple_name(tmp_path: Path) -> None:
    files = {"K.cs": "class Outer { class Inner { } void M() { new Inner(); } }\n"}
    assert edges(tmp_path, files) == {("Outer.M", "Outer.Inner", "instantiate", "same_file")}


def test_type_named_like_its_file_outside_any_namespace(tmp_path: Path) -> None:
    # K.cs has no namespace, so its module symbol is also named `K`; type lookups must skip it
    files = {
        "K.cs": "class K { void A() { B(); } void B() { } }\n",
        "U.cs": "class U { void M(K k) { k.A(); } }\n",
    }
    assert edges(tmp_path, files) == {
        ("K.A", "K.B", "call", "same_file"),
        ("U.M", "K.A", "call", "declared_type"),
    }


def test_name_fallback_matches_methods_for_calls_and_types_for_new(tmp_path: Path) -> None:
    files = {
        "Ext.cs": "namespace N;\nstatic class Ext { public static int UserId(this Principal p) => 1; }\n",
        "Ids.cs": "namespace N;\nrecord UserId(int Value);\n",
        "Api.cs": "namespace N;\nclass Api { void List() { } }\n",
        "Use.cs": "namespace N;\nclass U { void M(ClaimsPrincipal user) { user.UserId(); var x = new List<int>(); } }\n",
    }
    assert edges(tmp_path, files) == {("N.U.M", "N.Ext.UserId", "call", "repo_unique")}
