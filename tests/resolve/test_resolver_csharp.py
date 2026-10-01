from pathlib import Path

from citegraph.resolve.rules import is_stoplisted
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
