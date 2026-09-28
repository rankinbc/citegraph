from collections.abc import Callable
from pathlib import Path

from citegraph.evaluation.corpus import CorpusRepo, fetch_corpus, load_corpus
from tests.helpers import commit_all, git, write_files

MakeRepo = Callable[[str, dict[str, str]], Path]


def test_fetch_pins_exact_sha(make_repo: MakeRepo, tmp_path: Path) -> None:
    upstream = make_repo("upstream", {"a.py": "x = 1\n"})
    first = git(upstream, "rev-parse", "HEAD")
    write_files(upstream, {"a.py": "x = 2\n"})
    commit_all(upstream, "second")
    repos = [CorpusRepo(name="up", url=str(upstream), sha=first, lang="python")]
    dest = tmp_path / "corpus"
    [path] = fetch_corpus(repos, dest)
    assert git(path, "rev-parse", "HEAD") == first
    fetch_corpus(repos, dest)  # idempotent
    assert git(path, "rev-parse", "HEAD") == first


def test_load_corpus(tmp_path: Path) -> None:
    f = tmp_path / "corpus.yaml"
    f.write_text(
        "repos:\n  - {name: a, url: https://example.invalid/a, sha: '" + "1" * 40 + "', lang: python}\n",
        encoding="utf-8",
    )
    assert load_corpus(f)[0].name == "a"
