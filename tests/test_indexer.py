from collections.abc import Callable
from pathlib import Path

import pytest

import citegraph.indexer as indexer_mod
from citegraph.indexer import index_root
from citegraph.ingest import IngestError
from citegraph.store import Store
from tests.fixtures.sample_corpus import BILLING, SAMPLE_EDGES, SHOP
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
