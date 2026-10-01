from collections.abc import Callable
from pathlib import Path

import pytest

import citegraph.ingest as ingest_mod
from citegraph.config import CitegraphConfig, load_config
from citegraph.ingest import IngestError, discover_repos, head_sha, language_for, scan_repo
from tests.helpers import git

MakeRepo = Callable[[str, dict[str, str]], Path]

GIT_FAILURES = [
    (FileNotFoundError(2, "No such file or directory", "git"), "could not run git"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "not valid UTF-8"),
]


@pytest.mark.parametrize(("error", "message"), GIT_FAILURES, ids=["git-missing", "undecodable"])
def test_git_failures_become_ingest_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(ingest_mod.subprocess, "run", fail)
    with pytest.raises(IngestError, match=message):
        head_sha(tmp_path)


def test_discovers_child_repos_sorted(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("zeta", {"a.py": "x = 1\n"})
    make_repo("alpha", {"a.py": "x = 1\n"})
    (repos_root / "not-a-repo").mkdir()
    assert [p.name for p in discover_repos(repos_root)] == ["alpha", "zeta"]


def test_root_that_is_a_repo(make_repo: MakeRepo) -> None:
    repo = make_repo("solo", {"a.py": "x = 1\n"})
    assert discover_repos(repo) == [repo.resolve()]


def test_scan_lists_only_tracked_supported_files(make_repo: MakeRepo) -> None:
    repo = make_repo(
        "shop",
        {
            "src/shop/orders.py": "def f():\n    pass\n",
            "README.md": "# shop\n",
            "appsettings.json": "{}\n",
            ".env.example": "A=1\n",
            "node_modules/pkg/index.py": "x = 1\n",
        },
    )
    (repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")
    info = scan_repo(repo, CitegraphConfig())
    assert info.name == "shop"
    assert len(info.head_sha) == 40
    assert {f.rel_path: f.lang for f in info.files} == {
        "src/shop/orders.py": "python",
        "appsettings.json": "config",
        ".env.example": "config",
    }


def test_paths_are_posix(make_repo: MakeRepo) -> None:
    repo = make_repo("p", {"pkg/sub/mod.py": "x = 1\n"})
    assert [f.rel_path for f in scan_repo(repo, CitegraphConfig()).files] == ["pkg/sub/mod.py"]


def test_include_and_exclude_globs(make_repo: MakeRepo) -> None:
    repo = make_repo("g", {"src/a.py": "", "tests/test_a.py": "", "scripts/b.py": ""})
    cfg = CitegraphConfig(include=["src/**", "tests/**"], exclude=["tests/**"])
    assert [f.rel_path for f in scan_repo(repo, cfg).files] == ["src/a.py"]


def test_repo_without_commits_raises(repos_root: Path) -> None:
    empty = repos_root / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(IngestError, match="no commits") as excinfo:
        scan_repo(empty, CitegraphConfig())
    assert "rev-parse failed in" in str(excinfo.value)


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a/b.py", "python"),
        ("src/Api/OrdersController.cs", "csharp"),
        ("appsettings.Development.json", "config"),
        ("docker-compose.yml", "config"),
        ("pyproject.toml", "config"),
        ("app.config.yaml", "config"),
        ("x.ts", None),
        ("notes.json", None),
    ],
)
def test_language_for(path: str, lang: str | None) -> None:
    assert language_for(path) == lang


def test_load_config_reads_toml(tmp_path: Path) -> None:
    (tmp_path / "citegraph.toml").write_text(
        'exclude = ["legacy/**"]\nextra_redaction_patterns = ["ACME-[0-9]+"]\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.exclude == ["legacy/**"]
    assert cfg.extra_redaction_patterns == ["ACME-[0-9]+"]
    assert load_config(tmp_path / "missing").languages == ["python", "csharp"]
