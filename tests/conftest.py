from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.helpers import commit_all, git, write_files


@pytest.fixture(autouse=True)
def citegraph_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cg-home"
    monkeypatch.setenv("CITEGRAPH_HOME", str(home))
    return home


@pytest.fixture
def repos_root(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    root.mkdir()
    return root


@pytest.fixture
def make_repo(repos_root: Path) -> Callable[[str, dict[str, str]], Path]:
    def _make(name: str, files: dict[str, str]) -> Path:
        repo = repos_root / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "core.autocrlf", "false")
        write_files(repo, files)
        commit_all(repo, "init")
        return repo

    return _make
