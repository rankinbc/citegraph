import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

import citegraph.indexer as indexer_mod
from citegraph.indexer import index_root
from citegraph.ingest import IngestError
from citegraph.resolve.rules import CONFIDENCE
from citegraph.store import Store
from tests.fixtures.sample_corpus import BILLING, SAMPLE_EDGES, SHOP
from tests.fixtures.sample_corpus_csharp import CSHARP_SAMPLE_EDGES, ORDERING, PAYMENTS
from tests.helpers import commit_all, edge_set, git, load_secret_cases, write_files

MakeRepo = Callable[[str, dict[str, str]], Path]


@pytest.fixture
def sample_root(make_repo: MakeRepo, repos_root: Path) -> Path:
    make_repo("shop", SHOP)
    make_repo("billing", BILLING)
    return repos_root


def open_index(path: str) -> Store:
    return Store.open(Path(path))


def test_full_index(sample_root: Path, citegraph_home: Path) -> None:
    stats = index_root(sample_root)
    assert stats.repos == 2
    assert stats.files_changed == 10
    assert stats.edges == len(SAMPLE_EDGES)
    assert stats.leak_scan_clean is True
    assert Path(stats.db_path).parent == citegraph_home / "indexes"
    assert edge_set(open_index(stats.db_path)) == SAMPLE_EDGES


def test_reindex_without_changes_skips_work(sample_root: Path) -> None:
    index_root(sample_root)
    again = index_root(sample_root)
    assert again.files_changed == 0
    assert again.resolved is False
    assert again.edges == len(SAMPLE_EDGES)


def test_confidence_change_re_resolves_without_file_changes(
    sample_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_root(sample_root)
    monkeypatch.setitem(CONFIDENCE, "same_file", 0.93)
    again = index_root(sample_root)
    assert again.files_changed == 0
    assert again.resolved is True
    rows = open_index(again.db_path).conn.execute(
        "SELECT DISTINCT confidence FROM edges WHERE rule = 'same_file'"
    )
    assert [r["confidence"] for r in rows] == [0.93]


def test_redaction_change_rewrites_unchanged_files(sample_root: Path) -> None:
    first = index_root(sample_root)
    config = 'extra_redaction_patterns = ["render_[a-z]+"]\n'
    (sample_root / "citegraph.toml").write_text(config, encoding="utf-8")
    again = index_root(sample_root)
    assert again.files_changed == first.files_changed
    assert again.resolved is True
    rows = open_index(again.db_path).conn.execute("SELECT qualified_name FROM symbols")
    names = [r["qualified_name"] for r in rows]
    assert "billing.invoices.<redacted:custom>" in names
    assert not any("render_invoice" in n for n in names)
    assert index_root(sample_root).files_changed == 0


def test_new_extra_pattern_matching_a_stored_path_leaves_one_redacted_row(
    make_repo: MakeRepo, repos_root: Path
) -> None:
    make_repo("ledger", {"src/acme_internal_ledger/core.py": "def post_entry():\n    pass\n"})
    index_root(repos_root)
    config = 'extra_redaction_patterns = ["acme_internal_[a-z_]+"]\n'
    (repos_root / "citegraph.toml").write_text(config, encoding="utf-8")
    for _ in range(3):
        stats = index_root(repos_root)
        assert stats.leak_scan_clean is True
        assert b"acme_internal_ledger" not in Path(stats.db_path).read_bytes().lower()
        store = open_index(stats.db_path)
        try:
            paths = [r["path"] for r in store.conn.execute("SELECT path FROM files")]
        finally:
            store.close()
        assert len(paths) == 1
        assert re.fullmatch(r"src/<redacted:custom~[0-9a-f]{8}>/core\.py", paths[0])


def test_rows_deleted_by_a_rewrite_leave_no_bytes_behind(make_repo: MakeRepo, repos_root: Path) -> None:
    # enough rows that the old cells sit in freed space rather than being overwritten by the new rows
    make_repo("ledger", {f"src/acme_internal_ledger/m{i}.py": "def f():\n    pass\n" for i in range(10)})
    index_root(repos_root)
    config = 'extra_redaction_patterns = ["acme_internal_[a-z_]+"]\n'
    (repos_root / "citegraph.toml").write_text(config, encoding="utf-8")
    stats = index_root(repos_root)
    assert stats.leak_scan_clean is True
    assert b"acme_internal_ledger" not in Path(stats.db_path).read_bytes().lower()


# A folder name whose first trigrams occur nowhere else in the fixture (symbols fn_a ... fn_e) and not in its
# redacted form, so any of them left in the file is residue of the raw name.
LEDGER_FILES = {
    f"src/qzxv_internal_ledger/m{i}.py": "".join(f"def fn_{c}():\n    pass\n\n" for c in "abcde")
    for i in range(6)
}
LEDGER_PATTERN = 'extra_redaction_patterns = ["qzxv_internal_[a-z_]+"]\n'
LEDGER_TRIGRAMS = (b"qzx", b"zxv", b"xv_")


def ledger_residue(db_path: str) -> list[bytes]:
    data = Path(db_path).read_bytes().lower()
    return [t for t in (b"qzxv_internal_ledger", *LEDGER_TRIGRAMS) if t in data]


def test_a_rewrite_leaves_no_trigram_of_a_redacted_name_in_the_fts_index(
    make_repo: MakeRepo, repos_root: Path
) -> None:
    make_repo("ledger", LEDGER_FILES)
    index_root(repos_root)
    (repos_root / "citegraph.toml").write_text(LEDGER_PATTERN, encoding="utf-8")
    stats = index_root(repos_root)
    assert stats.leak_scan_clean is True
    assert ledger_residue(stats.db_path) == []


def test_a_rewrite_clears_residue_of_an_index_built_without_secure_delete(
    make_repo: MakeRepo, repos_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_store = Store.open

    # the index as releases before secure_delete wrote it
    def open_without_secure_delete(path: Path) -> Store:
        store = open_store(path)
        store.conn.execute("PRAGMA secure_delete=OFF")
        return store

    make_repo("ledger", LEDGER_FILES)
    with monkeypatch.context() as patch:
        patch.setattr(Store, "open", staticmethod(open_without_secure_delete))
        index_root(repos_root)
    (repos_root / "citegraph.toml").write_text(LEDGER_PATTERN, encoding="utf-8")
    stats = index_root(repos_root)
    assert stats.leak_scan_clean is True
    assert ledger_residue(stats.db_path) == []
    conn = sqlite3.connect(stats.db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


@pytest.mark.parametrize("edit", ["delete", "change"])
def test_deleted_symbols_leave_no_trigram_in_the_fts_index(
    make_repo: MakeRepo, repos_root: Path, edit: str
) -> None:
    text = "".join(f"def qzxv_post_{c}():\n    pass\n\n" for c in "abcde")
    repo = make_repo("ledger", {"src/ledger/old.py": text, "src/ledger/keep.py": "def keep():\n    pass\n"})
    index_root(repos_root)
    if edit == "delete":
        (repo / "src/ledger/old.py").unlink()
    else:
        write_files(repo, {"src/ledger/old.py": text.replace("qzxv_post_", "fn_")})
    commit_all(repo, edit)
    stats = index_root(repos_root)
    assert stats.files_changed + stats.files_deleted == 1
    assert ledger_residue(stats.db_path) == []


def test_incremental_matches_full_reindex(sample_root: Path) -> None:
    index_root(sample_root, name="inc")
    billing = sample_root / "billing"
    renamed = BILLING["src/billing/invoices.py"].replace("render_invoice", "draw_invoice")
    write_files(billing, {"src/billing/invoices.py": renamed})
    (billing / "src/billing/notifications.py").unlink()
    commit_all(billing, "rename and delete")
    inc = index_root(sample_root, name="inc")
    assert inc.files_changed == 1
    assert inc.files_deleted == 1
    full = index_root(sample_root, name="full")
    inc_edges, full_edges = edge_set(open_index(inc.db_path)), edge_set(open_index(full.db_path))
    assert inc_edges == full_edges
    assert (
        "billing.invoices.create_invoice",
        "billing.invoices.draw_invoice",
        "call",
        "same_file",
    ) in inc_edges
    assert not any(t == "billing.notifications.notify_customer" for _, t, _, _ in inc_edges)


def test_csharp_repos_index_end_to_end(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("ordering", ORDERING)
    make_repo("payments", PAYMENTS)
    stats = index_root(repos_root)
    assert (stats.repos, stats.parse_errors, stats.leak_scan_clean) == (2, 0, True)
    store = open_index(stats.db_path)
    assert edge_set(store) == CSHARP_SAMPLE_EDGES
    langs = {r["lang"] for r in store.conn.execute("SELECT DISTINCT lang FROM files")}
    assert langs == {"csharp", "config"}


def test_csharp_can_be_turned_off_in_config(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("ordering", ORDERING)
    (repos_root / "citegraph.toml").write_text('languages = ["python"]\n', encoding="utf-8")
    store = open_index(index_root(repos_root).db_path)
    assert [r["lang"] for r in store.conn.execute("SELECT DISTINCT lang FROM files")] == ["config"]


def test_schema_change_rewrites_unchanged_files(sample_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = index_root(sample_root)
    monkeypatch.setattr(indexer_mod, "SCHEMA_VERSION", "999")
    again = index_root(sample_root)
    assert again.files_changed == first.files_changed
    assert edge_set(open_index(again.db_path)) == SAMPLE_EDGES


def test_large_file_is_skipped(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("big", {"small.py": "def f():\n    pass\n", "huge.py": "x = 1\n" * 400_000})
    stats = index_root(repos_root)
    assert stats.skipped_large == 1
    paths = [r["path"] for r in open_index(stats.db_path).conn.execute("SELECT path FROM files")]
    assert paths == ["small.py"]


def test_root_without_repos_raises_ingest_error(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="no git repositories"):
        index_root(tmp_path)


def test_repo_without_commits_is_skipped_with_warning(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("shop", SHOP)
    fresh = repos_root / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q")
    stats = index_root(repos_root)
    assert stats.repos == 1
    assert any("fresh" in w and "no commits" in w for w in stats.warnings)


def test_transient_scan_failure_keeps_previously_indexed_repo(sample_root: Path) -> None:
    index_root(sample_root)
    billing_head = sample_root / "billing" / ".git" / "HEAD"
    billing_head.write_text("ref: refs/heads/does-not-exist\n", encoding="utf-8")
    again = index_root(sample_root)
    assert any("billing" in w for w in again.warnings)
    store = open_index(again.db_path)
    row = store.conn.execute(
        "SELECT 1 FROM symbols WHERE qualified_name = ?", ("billing.invoices.create_invoice",)
    ).fetchone()
    assert row is not None


def test_process_pool_path_matches_inline(sample_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexer_mod, "PARALLEL_THRESHOLD", 0)
    stats = index_root(sample_root, name="pool", jobs=2)
    assert edge_set(open_index(stats.db_path)) == SAMPLE_EDGES


def test_no_secret_reaches_the_database(make_repo: MakeRepo, repos_root: Path) -> None:
    secrets = [c.value for c in load_secret_cases() if c.must_match]
    one_line = [s for s in secrets if "\n" not in s]
    literals = "\n".join(f"S{i} = {s!r}" for i, s in enumerate(secrets))
    env_reads = "\n".join(f"    os.environ.get({s!r})" for s in one_line)
    make_repo(
        "leaky",
        {
            "src/leaky/settings.py": f"import os\n{literals}\n\n\ndef load():\n{env_reads}\n",
            "appsettings.json": "{\n"
            + ",\n".join(f'  "{s}": "{s}"' for s in one_line if '"' not in s)
            + "\n}\n",
            f"src/leaky/notes_{one_line[0]}.py": "def f():\n    pass\n",
        },
    )
    stats = index_root(repos_root)
    assert stats.leak_scan_clean is True
    db_bytes = Path(stats.db_path).read_bytes().lower()
    for secret in secrets:
        # lower-cased on both sides: normalized config keys must not carry a secret in lower case
        assert secret.lower().encode() not in db_bytes


def test_two_files_whose_paths_redact_alike_keep_their_own_rows(
    make_repo: MakeRepo, repos_root: Path
) -> None:
    make_repo(
        "ledger",
        {
            "src/acme_internal_a/core.py": "def post_a():\n    pass\n",
            "src/acme_internal_b/core.py": "def post_b():\n    pass\n",
        },
    )
    (repos_root / "citegraph.toml").write_text(
        'extra_redaction_patterns = ["acme_internal_[a-z_]+"]\n', "utf-8"
    )
    stats = index_root(repos_root)
    assert stats.leak_scan_clean is True
    store = open_index(stats.db_path)
    try:
        rows = store.conn.execute(
            "SELECT f.path, s.name FROM files f JOIN symbols s ON s.file_id = f.id WHERE s.kind = 'function'"
        ).fetchall()
    finally:
        store.close()
    assert sorted(r["name"] for r in rows) == ["post_a", "post_b"]
    paths = {r["path"] for r in rows}
    assert len(paths) == 2
    assert all(p.startswith("src/<redacted:custom~") and p.endswith(">/core.py") for p in paths)
    assert index_root(repos_root).files_changed == 0
