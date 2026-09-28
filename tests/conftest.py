from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from citegraph.query.common import QueryContext
from citegraph.store import Store
from tests.fixtures.sample_corpus import SAMPLE
from tests.helpers import build_store, commit_all, git, write_files


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


@pytest.fixture
def sample_store(tmp_path: Path) -> Store:
    return build_store(tmp_path / "sample.db", SAMPLE)


@pytest.fixture
def sample_ctx(sample_store: Store) -> QueryContext:
    return QueryContext(sample_store, head_fn=lambda _path: "0" * 40)
