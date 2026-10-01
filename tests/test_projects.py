"""Projects inside a repository: opt-in splitting of a monorepo into logical repos."""

from collections.abc import Callable
from pathlib import Path

from citegraph.indexer import index_root
from citegraph.ingest import project_roots
from citegraph.store import Store
from tests.helpers import edge_set

MakeRepo = Callable[[str, dict[str, str]], Path]

MONO = {
    "pyproject.toml": '[project]\nname = "mono"\n',
    "scripts/tool.py": "def main():\n    pass\n",
    "components/api/requirements.txt": "fastapi\n",
    "components/api/app/__init__.py": "",
    "components/api/app/util.py": "def helper():\n    pass\n",
    "components/worker/requirements.txt": "dramatiq\n",
    "components/worker/app/__init__.py": "",
    "components/worker/app/util.py": "def helper():\n    pass\n",
    "components/worker/app/jobs.py": "from app.util import helper\n\n\ndef run():\n    helper()\n",
    "components/bff/Shop.sln": "",
    "components/bff/src/Api/Api.csproj": "<Project />\n",
    "components/bff/src/Api/C.cs": "namespace Api;\nclass C { void M() { Lib.D.Go(); } }\n",
    "components/bff/src/Lib/Lib.csproj": "<Project />\n",
    "components/bff/src/Lib/D.cs": "namespace Lib;\npublic static class D { public static void Go() { } }\n",
}


def repo_names(store: Store) -> set[str]:
    return {str(r["name"]) for r in store.conn.execute("SELECT name FROM repos")}


def edge_repos(store: Store) -> set[tuple[str, str, str, str]]:
    sql = (
        "SELECT fs.qualified_name AS f, fr.name AS fr, ts.qualified_name AS t, tr.name AS tr FROM edges e "
        "JOIN symbols fs ON fs.id = e.from_symbol_id JOIN files ff ON ff.id = fs.file_id "
        "JOIN repos fr ON fr.id = ff.repo_id JOIN symbols ts ON ts.id = e.to_symbol_id "
        "JOIN files tf ON tf.id = ts.file_id JOIN repos tr ON tr.id = tf.repo_id"
    )
    return {(r["f"], r["fr"], r["t"], r["tr"]) for r in store.conn.execute(sql)}


def test_project_roots_auto_detection() -> None:
    assert project_roots(list(MONO), "auto") == ["components/api", "components/bff", "components/worker"]
    # a .csproj without a .sln above it is a project of its own; the repository root never is
    assert project_roots(["setup.py", "tools/x/X.csproj", "tools/x/X.cs"], "auto") == ["tools/x"]
    assert project_roots(list(MONO), "off") == []
    assert project_roots(list(MONO), ["components/worker/"]) == ["components/worker"]


def test_auto_projects_split_a_monorepo(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("mono", MONO)
    (repos_root / "citegraph.toml").write_text('projects = "auto"\n', encoding="utf-8")
    stats = index_root(repos_root)
    store = Store.open(Path(stats.db_path))
    assert repo_names(store) == {
        "mono",
        "mono/components/api",
        "mono/components/bff",
        "mono/components/worker",
    }
    edges = edge_repos(store)
    # module names start at the project folder, and `app` resolves inside the worker, not the api
    assert ("app.jobs.run", "mono/components/worker", "app.util.helper", "mono/components/worker") in edges
    assert ("Api.C.M", "mono/components/bff", "Lib.D.Go", "mono/components/bff") in edges
    paths = {
        (str(r["name"]), str(r["path"]))
        for r in store.conn.execute("SELECT r.name, f.path FROM files f JOIN repos r ON r.id = f.repo_id")
    }
    assert ("mono/components/worker", "app/jobs.py") in paths
    assert ("mono", "scripts/tool.py") in paths
    assert index_root(repos_root).files_changed == 0


def test_explicit_projects_and_an_unknown_folder(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("mono", MONO)
    (repos_root / "citegraph.toml").write_text(
        'projects = ["components/worker", "missing/dir"]\n', encoding="utf-8"
    )
    stats = index_root(repos_root)
    assert repo_names(Store.open(Path(stats.db_path))) == {"mono", "mono/components/worker"}
    assert any("missing/dir" in w for w in stats.warnings)


def test_projects_off_by_default(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("mono", MONO)
    store = Store.open(Path(index_root(repos_root).db_path))
    assert repo_names(store) == {"mono"}
    # without projects the worker's `from app.util import helper` cannot match `components.worker.app.util`
    assert not any(f == "components.worker.app.jobs.run" for f, _, _, _ in edge_set(store))
