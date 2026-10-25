"""Star re-exports and queue links through constants stored as digests."""

from pathlib import Path

from tests.helpers import build_store, edge_set


def edges(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str, str]]:
    return edge_set(build_store(tmp_path / "i.db", {"a": files}))


def test_call_through_a_star_reexport(tmp_path: Path) -> None:
    files = {
        "pkg/__init__.py": "from .impl import *\n",
        "pkg/impl.py": "def work():\n    pass\n\n\ndef _hidden():\n    pass\n",
        "main.py": "from pkg import work\nimport pkg\n\n\ndef run():\n    work()\n    pkg.work()\n    pkg._hidden()\n",
        "direct.py": "from pkg.impl import *\n\n\ndef go():\n    work()\n",
    }
    found = edges(tmp_path, files)
    assert ("main.run", "pkg.impl.work", "call", "import_scope") in found
    assert ("direct.go", "pkg.impl.work", "call", "import_scope") in found
    assert not any(t == "pkg.impl._hidden" and rule == "import_scope" for _, t, _, rule in found)


def test_star_chain_and_cycle(tmp_path: Path) -> None:
    files = {
        "top/__init__.py": "from top.mid import *\n",
        "top/mid/__init__.py": "from top.mid.core import *\n",
        "top/mid/core.py": "def engine():\n    pass\n",
        "a/__init__.py": "from b import *\n",
        "b/__init__.py": "from a import *\n",
        "app.py": "import top\nimport a\n\n\ndef go():\n    top.engine()\n    a.nothing()\n",
    }
    found = edges(tmp_path, files)
    assert ("app.go", "top.mid.core.engine", "call", "import_scope") in found
    assert not any(t.endswith("nothing") for _, t, _, _ in found)


def test_csharp_constant_send_still_reaches_the_python_actor(tmp_path: Path) -> None:
    files = {
        "bff/Tasks.cs": 'namespace Shop.Jobs;\npublic static class Tasks { public const string Classify = "classify_stems"; }\n',
        "bff/Api.cs": (
            "using Shop.Jobs;\nnamespace Shop.Api;\npublic class Endpoints\n{\n"
            "    public async Task Go(IJobQueue queue) => await queue.EnqueueAsync(Tasks.Classify, new object[0]);\n"
            "}\n"
        ),
        "worker/jobs.py": '@dramatiq.actor(actor_name="classify_stems")\ndef classify(version_id):\n    pass\n',
    }
    store = build_store(tmp_path / "i.db", {"a": files})
    assert ("Shop.Api.Endpoints.Go", "worker.jobs.classify", "call", "queue_match") in edge_set(store)
    values = [str(r["value"]) for r in store.conn.execute("SELECT value FROM string_consts")]
    assert values and "classify_stems" not in values
