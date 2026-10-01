import sqlite3
from pathlib import Path

import pytest

from citegraph.models import ConfigKey, ExtractResult, Reference, Symbol, ToolError
from citegraph.store import EdgeRow, Store
from citegraph.store.schema import SCHEMA_VERSION
from tests.helpers import load_secret_cases

SECRET = next(c for c in load_secret_cases() if c.kind == "github-token").value


def result_with(name: str) -> ExtractResult:
    return ExtractResult(
        symbols=[
            Symbol(kind="module", name="m", qualified_name="m", line_start=1, line_end=3),
            Symbol(
                kind="function",
                name=name,
                qualified_name=f"m.{name}",
                line_start=1,
                line_end=2,
                param_count=0,
            ),
        ],
        config_keys=[ConfigKey(key_path="Payment:ApiUrl", line=3, origin="json")],
    )


def test_upsert_file_replaces_previous_rows(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    store.upsert_file(repo, "m.py", "python", "h1", 3, result_with("f"), module="m")
    store.upsert_file(repo, "m.py", "python", "h2", 3, result_with("g"), module="m")
    store.commit()
    names = [r["name"] for r in store.conn.execute("SELECT name FROM symbols ORDER BY name")]
    assert names == ["g", "m"]
    assert {path: row.content_hash for path, row in store.stored_files(repo).items()} == {"m.py": "h2"}
    assert store.count("config_keys") == 1
    norm = store.conn.execute("SELECT key_norm FROM config_keys").fetchone()["key_norm"]
    assert norm == "payment:apiurl"


def test_delete_file_cascades(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    store.upsert_file(repo, "m.py", "python", "h1", 3, result_with("f"), module="m")
    store.delete_file(repo, "m.py")
    store.commit()
    assert store.count("symbols") == 0
    assert store.count("config_keys") == 0


def test_every_write_is_sanitized(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    res = ExtractResult(config_keys=[ConfigKey(key_path=SECRET, line=1, origin="code-read")])
    store.upsert_file(repo, f"cfg/{SECRET}.json", "config", "h", 1, res, module=None)
    store.commit()
    store.checkpoint()
    store.close()
    assert SECRET.encode() not in (tmp_path / "i.db").read_bytes()


def test_fts_finds_symbols_by_substring(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    store.upsert_file(repo, "m.py", "python", "h", 3, result_with("place_order"), module="m")
    store.commit()
    rows = store.conn.execute(
        "SELECT s.name FROM symbols_fts JOIN symbols s ON s.id = symbols_fts.rowid WHERE symbols_fts MATCH ?",
        ('"ace_ord"',),
    ).fetchall()
    assert [r["name"] for r in rows] == ["place_order"]


def test_replace_edges_and_runs(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    store.upsert_file(repo, "m.py", "python", "h", 3, result_with("f"), module="m")
    ids = [r["id"] for r in store.conn.execute("SELECT id FROM symbols ORDER BY id")]
    store.conn.execute(
        "INSERT INTO refs(file_id, from_qualified, to_name, kind, line) VALUES (1, 'm', 'f', 'call', 3)"
    )
    assert store.replace_edges([EdgeRow(1, ids[0], ids[1], "call", "same_file", 0.95, 1)]) == 1
    run = store.start_run()
    store.finish_run(run, {"shop": "a" * 40}, {"symbols": 2}, parse_errors=0, leak_scan_clean=True)
    store.commit()
    row = store.conn.execute("SELECT * FROM index_runs").fetchone()
    assert row["leak_scan_clean"] == 1
    assert row["finished"] is not None


def test_delete_repos_not_in(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    store.upsert_repo("a", "/r/a", "a" * 40)
    store.upsert_repo("b", "/r/b", "b" * 40)
    assert store.delete_repos_not_in(["a"]) == 1
    assert [r["name"] for r in store.conn.execute("SELECT name FROM repos")] == ["a"]


def test_read_only_store_refuses_writes(tmp_path: Path) -> None:
    Store.open(tmp_path / "i.db").close()
    ro = Store.open_read_only(tmp_path / "i.db")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.conn.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")


def test_read_only_missing_index_is_not_indexed(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as info:
        Store.open_read_only(tmp_path / "missing.db")
    assert info.value.code == "not_indexed"


def test_receiver_type_is_stored(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "i.db")
    repo = store.upsert_repo("shop", "/r/shop", "a" * 40)
    result = result_with("f")
    result.references.append(
        Reference(from_qualified="m.f", to_name="Save", kind="call", line=2, receiver_type="IRepo")
    )
    store.upsert_file(repo, "m.cs", "csharp", "h", 3, result, module="m")
    row = store.conn.execute("SELECT to_name, receiver_type FROM refs").fetchone()
    assert (row["to_name"], row["receiver_type"]) == ("Save", "IRepo")


def test_open_adds_receiver_type_to_an_index_from_an_older_release(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE refs(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, from_qualified TEXT NOT NULL, "
        "to_name TEXT NOT NULL, kind TEXT NOT NULL, line INTEGER NOT NULL)"
    )
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()
    store = Store.open(path)
    columns = {r["name"] for r in store.conn.execute("PRAGMA table_info(refs)")}
    assert "receiver_type" in columns
    assert store.get_meta("schema_version") == SCHEMA_VERSION
