from collections.abc import Callable
from pathlib import Path

import pytest

import citegraph.evaluation.corpus as corpus_mod
from citegraph.evaluation.corpus import CorpusError, CorpusRepo, fetch_corpus, load_corpus
from tests.helpers import commit_all, git, write_files

MakeRepo = Callable[[str, dict[str, str]], Path]

GIT_FAILURES = [
    (FileNotFoundError(2, "No such file or directory", "git"), "could not run git"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "could not be decoded"),
]


@pytest.mark.parametrize(("error", "message"), GIT_FAILURES, ids=["git-missing", "undecodable"])
@pytest.mark.parametrize("cloned", [False, True], ids=["fresh", "already-cloned"])
def test_git_failures_become_corpus_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message: str, cloned: bool
) -> None:
    if cloned:  # the first git call is then `cat-file`, not `clone`
        (tmp_path / "r" / ".git").mkdir(parents=True)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(corpus_mod.subprocess, "run", fail)
    repos = [CorpusRepo(name="r", url="https://example.invalid/r.git", sha="0" * 40, lang="python")]
    with pytest.raises(CorpusError, match=message):
        fetch_corpus(repos, tmp_path)


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


def test_fetch_populates_working_tree_at_own_head(make_repo: MakeRepo, tmp_path: Path) -> None:
    upstream = make_repo("upstream", {"a.py": "x = 1\n"})
    sha = git(upstream, "rev-parse", "HEAD")
    repos = [CorpusRepo(name="up", url=str(upstream), sha=sha, lang="python")]
    dest = tmp_path / "corpus"
    [path] = fetch_corpus(repos, dest)
    assert (path / "a.py").is_file()


def test_fetch_recovers_from_interrupted_clone(make_repo: MakeRepo, tmp_path: Path) -> None:
    upstream = make_repo("upstream", {"a.py": "x = 1\n"})
    sha = git(upstream, "rev-parse", "HEAD")
    repos = [CorpusRepo(name="up", url=str(upstream), sha=sha, lang="python")]
    dest = tmp_path / "corpus"
    dest.mkdir()
    path = dest / "up"
    # simulate a run interrupted between the --no-checkout clone and the checkout
    git(tmp_path, "clone", "--quiet", "--no-checkout", str(upstream), str(path))
    assert not (path / "a.py").exists()
    fetch_corpus(repos, dest)
    assert (path / "a.py").is_file()


def test_load_corpus(tmp_path: Path) -> None:
    f = tmp_path / "corpus.yaml"
    f.write_text(
        "repos:\n  - {name: a, url: https://example.invalid/a, sha: '" + "1" * 40 + "', lang: python}\n",
        encoding="utf-8",
    )
    assert load_corpus(f)[0].name == "a"
