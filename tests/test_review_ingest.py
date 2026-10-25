import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from citegraph.config import CitegraphConfig
from citegraph.indexer import index_root
from citegraph.ingest import MAX_DISCOVERY_DEPTH, discover_repos, scan_repo
from citegraph.query.common import git_head
from citegraph.store import Store
from tests.helpers import commit_all, git, write_files

MakeRepo = Callable[[str, dict[str, str]], Path]


def _init_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "core.autocrlf", "false")
    write_files(path, files)
    commit_all(path, "init")
    return path


def _stored_paths(db_path: str) -> set[str]:
    store = Store.open_read_only(Path(db_path))
    try:
        return {str(r["path"]) for r in store.conn.execute("SELECT path FROM files")}
    finally:
        store.close()


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")


def _junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("NTFS junctions exist only on Windows")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_repo_git_config_fsmonitor_never_runs(make_repo: MakeRepo, repos_root: Path, tmp_path: Path) -> None:
    repo = make_repo("svc", {"a.py": "def a():\n    pass\n"})
    marker = tmp_path / "fsmonitor-ran.txt"
    hook = tmp_path / "fsmonitor_hook.py"
    hook.write_text(f"open({str(marker)!r}, 'a').write('ran')\n", encoding="utf-8")
    git(repo, "config", "core.fsmonitor", f'"{Path(sys.executable).as_posix()}" "{hook.as_posix()}"')
    index_root(repos_root)
    assert git_head(repo) is not None
    assert not marker.exists()
    # control: plain git in that repo does run the hook, so the assertions above are not vacuous
    subprocess.run(["git", "-C", str(repo), "ls-files"], check=True, capture_output=True)
    assert marker.exists()


def test_tracked_file_replaced_by_symlink_outside_root_is_skipped(
    make_repo: MakeRepo, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("def outside_fn():\n    pass\n", encoding="utf-8")
    repo = make_repo("svc", {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    (repo / "b.py").unlink()
    _symlink(repo / "b.py", outside / "secret.py")
    assert [f.rel_path for f in scan_repo(repo, CitegraphConfig()).files] == ["a.py"]


def test_folder_replaced_by_symlink_outside_root_is_skipped(make_repo: MakeRepo, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("def outside_fn():\n    pass\n", encoding="utf-8")
    repo = make_repo("svc", {"a.py": "x = 1\n", "sub/x.py": "def inside():\n    pass\n"})
    (repo / "sub" / "x.py").unlink()
    (repo / "sub").rmdir()
    _symlink(repo / "sub", outside)
    assert [f.rel_path for f in scan_repo(repo, CitegraphConfig()).files] == ["a.py"]


def test_folder_replaced_by_junction_outside_root_is_not_indexed(
    make_repo: MakeRepo, repos_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("def outside_fn():\n    pass\n", encoding="utf-8")
    repo = make_repo("svc", {"a.py": "x = 1\n", "sub/x.py": "def inside():\n    pass\n"})
    (repo / "sub" / "x.py").unlink()
    (repo / "sub").rmdir()
    _junction(repo / "sub", outside)
    stats = index_root(repos_root)
    assert _stored_paths(stats.db_path) == {"a.py"}


def test_discovery_walks_nested_folders_up_to_the_depth_limit(repos_root: Path) -> None:
    _init_repo(repos_root / "top", {"a.py": "x = 1\n"})
    _init_repo(repos_root / "team" / "api", {"a.py": "x = 1\n"})
    _init_repo(repos_root / "org" / "team" / "web", {"a.py": "x = 1\n"})
    too_deep = repos_root.joinpath(*[f"d{i}" for i in range(MAX_DISCOVERY_DEPTH)], "deep")
    _init_repo(too_deep, {"a.py": "x = 1\n"})
    found = [p.relative_to(repos_root.resolve()).as_posix() for p in discover_repos(repos_root)]
    assert found == ["org/team/web", "team/api", "top"]


def test_discovery_skips_tool_folders_and_does_not_descend_into_a_repo(repos_root: Path) -> None:
    outer = _init_repo(repos_root / "outer", {"a.py": "x = 1\n"})
    _init_repo(outer / "vendored", {"a.py": "x = 1\n"})
    _init_repo(repos_root / "node_modules" / "pkg", {"a.py": "x = 1\n"})
    _init_repo(repos_root / ".venv" / "src" / "dep", {"a.py": "x = 1\n"})
    assert [p.name for p in discover_repos(repos_root)] == ["outer"]


def test_discovery_does_not_follow_linked_folders(repos_root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    _init_repo(elsewhere / "foreign", {"a.py": "x = 1\n"})
    link = _junction if sys.platform == "win32" else _symlink
    link(repos_root / "folder-link", elsewhere)  # a linked folder holding a repo
    link(repos_root / "repo-link", elsewhere / "foreign")  # a link that is itself a repo
    assert discover_repos(repos_root) == []


def test_nested_repos_with_the_same_folder_name_are_indexed_apart(repos_root: Path) -> None:
    _init_repo(repos_root / "a" / "api", {"one.py": "def one():\n    pass\n"})
    _init_repo(repos_root / "b" / "api", {"two.py": "def two():\n    pass\n"})
    stats = index_root(repos_root)
    store = Store.open_read_only(Path(stats.db_path))
    try:
        rows = store.conn.execute("SELECT r.name, f.path FROM files f JOIN repos r ON r.id = f.repo_id")
        assert sorted((str(r[0]), str(r[1])) for r in rows) == [("a/api", "one.py"), ("b/api", "two.py")]
    finally:
        store.close()
    assert stats.repos == 2


def test_symlink_check_keeps_ordinary_files(make_repo: MakeRepo) -> None:
    repo = make_repo("svc", {"a.py": "x = 1\n", "pkg/b.py": "y = 2\n"})
    assert sorted(f.rel_path for f in scan_repo(repo, CitegraphConfig()).files) == ["a.py", "pkg/b.py"]
