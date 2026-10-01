from pathlib import Path

from citegraph.indexer import index_root
from citegraph.store import Store
from tests.helpers import build_store, edge_set
from tests.test_projects import MakeRepo, edge_repos


def edges(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str, str, str]]:
    return edge_set(build_store(tmp_path / "i.db", {"a": files}))


def test_call_through_a_package_reexport(tmp_path: Path) -> None:
    files = {
        "src/pkg/__init__.py": "from .impl import work\n",
        "src/pkg/impl.py": "def work():\n    pass\n",
        "app.py": "from pkg import work\n\n\ndef go():\n    work()\n",
    }
    assert ("app.go", "pkg.impl.work", "call", "import_scope") in edges(tmp_path, files)
    assert ("app", "pkg.impl.work", "import", "import_scope") in edges(tmp_path, files)


def test_two_hops_and_a_dotted_reference(tmp_path: Path) -> None:
    files = {
        "src/top/__init__.py": "from .mid import Engine\n",
        "src/top/mid/__init__.py": "from .core import Engine\n",
        "src/top/mid/core.py": "class Engine:\n    def start(self):\n        pass\n",
        "app.py": "import top\n\n\ndef go():\n    top.Engine.start(None)\n",
    }
    assert ("app.go", "top.mid.core.Engine.start", "call", "import_scope") in edges(tmp_path, files)


def test_reexport_cycle_and_missing_name_stay_unresolved(tmp_path: Path) -> None:
    files = {
        "src/a/__init__.py": "from b import thing\n",
        "src/b/__init__.py": "from a import thing\n",
        "src/c/__init__.py": "from .impl import absent\n",
        "src/c/impl.py": "def present():\n    pass\n",
        "app.py": "from a import thing\nfrom c import absent\n\n\ndef go():\n    thing()\n    absent()\n",
    }
    assert {(f, t) for f, t, kind, _ in edges(tmp_path, files) if kind == "call"} == set()


def test_reexport_prefers_the_referencing_project(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo(
        "mono",
        {
            "components/api/requirements.txt": "fastapi\n",
            "components/api/app/__init__.py": "from app.config import settings\n",
            "components/api/app/config.py": "def settings():\n    pass\n",
            "components/worker/requirements.txt": "dramatiq\n",
            "components/worker/app/__init__.py": "from app.core import settings\n",
            "components/worker/app/core.py": "def settings():\n    pass\n",
            "components/worker/app/jobs.py": "from app import settings\n\n\ndef run():\n    settings()\n",
        },
    )
    (repos_root / "citegraph.toml").write_text('projects = "auto"\n', encoding="utf-8")
    store = Store.open(Path(index_root(repos_root).db_path))
    assert ("app.jobs.run", "mono/components/worker", "app.core.settings", "mono/components/worker") in (
        edge_repos(store)
    )
