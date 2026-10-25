import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from citegraph.cli.main import main
from citegraph.home import index_path
from citegraph.models import ToolError
from citegraph.store import NewerIndexError, Store
from citegraph.store.schema import SCHEMA_VERSION
from tests.helpers import commit_all, git, write_files

NEWER = str(int(SCHEMA_VERSION) + 1)


def _newer_index(path: Path) -> bytes:
    store = Store.open(path)
    store._write("UPDATE meta SET value = ? WHERE key = 'schema_version'", (NEWER,))
    store._write("INSERT INTO meta(key, value) VALUES ('written_by', 'newer')")
    store.commit()
    store.checkpoint()
    store.close()
    return path.read_bytes()


def test_open_for_writing_refuses_a_newer_index_and_leaves_it_untouched(tmp_path: Path) -> None:
    db = tmp_path / "newer.db"
    before = _newer_index(db)
    with pytest.raises(
        NewerIndexError, match=re.escape(f"newer citegraph (schema {NEWER}); this is schema {SCHEMA_VERSION}")
    ):
        Store.open(db)
    assert db.read_bytes() == before
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == NEWER
    finally:
        conn.close()


def test_read_only_open_of_a_newer_index_is_not_indexed_naming_both_versions(tmp_path: Path) -> None:
    db = tmp_path / "newer.db"
    _newer_index(db)
    with pytest.raises(ToolError) as info:
        Store.open_read_only(db)
    assert info.value.code == "not_indexed"
    assert f"schema {NEWER}" in info.value.message and f"schema {SCHEMA_VERSION}" in info.value.message
    assert "CITEGRAPH_HOME" in info.value.hint


def test_index_command_refuses_a_newer_index(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "svc"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write_files(repo, {"a.py": "def a():\n    pass\n"})
    commit_all(repo, "init")
    db = index_path(repo)
    db.parent.mkdir(parents=True, exist_ok=True)
    before = _newer_index(db)
    result = CliRunner().invoke(main, ["index", str(repo)])
    assert result.exit_code != 0
    assert "newer citegraph" in result.output and "CITEGRAPH_HOME" in result.output
    assert db.read_bytes() == before


def test_an_older_index_is_still_upgraded_on_write(tmp_path: Path) -> None:
    db = tmp_path / "older.db"
    store = Store.open(db)
    store._write("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    store.commit()
    store.close()
    store = Store.open(db)
    try:
        assert store.get_meta("schema_version") == SCHEMA_VERSION
    finally:
        store.close()
