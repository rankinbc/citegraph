"""Find git repos under a root and list the files citegraph can read. Git calls use fixed argument lists."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from citegraph.config import CitegraphConfig

LANGUAGE_BY_EXT = {".py": "python", ".cs": "csharp"}
CONFIG_FILE_GLOBS = (
    "appsettings*.json",
    "config*.json",
    "*.config.yaml",
    "*.config.yml",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    ".env.example",
    ".env.sample",
    ".env.template",
    "pyproject.toml",
)
MAX_FILE_BYTES = 2_000_000
PYTHON_PROJECT_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"})


class IngestError(Exception):
    pass


class RepoFile(BaseModel):
    rel_path: str
    lang: str
    size: int


class RepoInfo(BaseModel):
    name: str
    path: Path
    head_sha: str
    files: list[RepoFile]


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except subprocess.CalledProcessError as exc:
        raise IngestError(f"git {args[0]} failed in {repo.name}: {exc.stderr.strip()}") from exc
    except OSError as exc:
        raise IngestError(f"could not run git ({exc}); install git and make sure it is on PATH") from exc
    except UnicodeDecodeError as exc:
        raise IngestError(f"git {args[0]} in {repo.name} printed output that is not valid UTF-8") from exc
    return out.stdout


def discover_repos(root: Path) -> list[Path]:
    root = root.resolve()
    if (root / ".git").exists():
        return [root]
    return sorted((p for p in root.iterdir() if p.is_dir() and (p / ".git").exists()), key=lambda p: p.name)


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def tracked_files(repo: Path) -> list[str]:
    return [p for p in _git(repo, "ls-files", "-z").split("\0") if p]


def language_for(rel_path: str) -> str | None:
    name = rel_path.rsplit("/", 1)[-1]
    if any(fnmatchcase(name, pattern) for pattern in CONFIG_FILE_GLOBS):
        return "config"
    return LANGUAGE_BY_EXT.get(PurePosixPath(rel_path).suffix)


def _matches(rel_path: str, pattern: str) -> bool:
    # "/" prefix lets "**/x/**" match a top-level "x/" directory
    return fnmatchcase(rel_path, pattern) or fnmatchcase("/" + rel_path, pattern)


def _selected(rel_path: str, config: CitegraphConfig) -> bool:
    if config.include and not any(_matches(rel_path, p) for p in config.include):
        return False
    return not any(_matches(rel_path, p) for p in config.exclude)


def scan_repo(repo: Path, config: CitegraphConfig) -> RepoInfo:
    try:
        sha = head_sha(repo)
    except IngestError as exc:
        raise IngestError(f"{repo.name}: no commits yet, or HEAD is unreadable: {exc}") from exc
    files: list[RepoFile] = []
    for rel in tracked_files(repo):
        lang = language_for(rel)
        if lang is None or (lang != "config" and lang not in config.languages):
            continue
        if not _selected(rel, config):
            continue
        path = repo / rel
        if path.is_file():
            files.append(RepoFile(rel_path=rel, lang=lang, size=path.stat().st_size))
    return RepoInfo(name=repo.name, path=repo, head_sha=sha, files=files)


def project_roots(tracked: list[str], projects: str | list[str]) -> list[str]:
    """Project folders inside a repository, as repository-relative paths.

    "auto": folders holding a Python marker file or a `.sln`, and folders holding a `.csproj` with no `.sln` above
    them; the repository root is never a project of its own. A list: those folders. "off": none.
    """
    if projects == "off":
        return []
    if not isinstance(projects, str):
        return sorted({p.strip("/") for p in projects if p.strip("/")})
    roots: set[str] = set()
    solutions: set[str] = set()
    csproj: set[str] = set()
    for rel in tracked:
        folder, _, name = rel.rpartition("/")
        if not folder:
            continue
        if name in PYTHON_PROJECT_MARKERS:
            roots.add(folder)
        elif name.endswith(".sln"):
            solutions.add(folder)
        elif name.endswith(".csproj"):
            csproj.add(folder)
    roots |= solutions
    roots |= {f for f in csproj if not any(f == s or f.startswith(s + "/") for s in solutions)}
    return sorted(roots)


def scan_projects(repo: Path, config: CitegraphConfig) -> tuple[list[RepoInfo], list[str]]:
    """The repository as logical repos, one per project folder plus one for the rest, and any warnings.

    A project's files are re-rooted at its folder, so module names and evidence paths start there.
    """
    info = scan_repo(repo, config)
    if config.projects == "off":
        return [info], []
    tracked = tracked_files(repo)
    roots = project_roots(tracked, config.projects)
    warnings: list[str] = []
    if not isinstance(config.projects, str):
        folders = {rel.rpartition("/")[0] for rel in tracked}
        known = {f for folder in folders for f in _parents(folder)}
        for root in roots:
            if root not in known:
                warnings.append(f"{repo.name}: project folder {root!r} holds no tracked files")
        roots = [r for r in roots if r in known]
    by_root: dict[str | None, list[RepoFile]] = defaultdict(list)
    deepest_first = sorted(roots, key=len, reverse=True)
    for file in info.files:
        root = next((r for r in deepest_first if file.rel_path.startswith(r + "/")), None)
        rel = file.rel_path[len(root) + 1 :] if root else file.rel_path
        by_root[root].append(RepoFile(rel_path=rel, lang=file.lang, size=file.size))
    infos = [RepoInfo(name=info.name, path=info.path, head_sha=info.head_sha, files=by_root[None])]
    infos += [
        RepoInfo(
            name=f"{info.name}/{root}", path=info.path / root, head_sha=info.head_sha, files=by_root[root]
        )
        for root in roots
    ]
    return infos, warnings


def _parents(folder: str) -> list[str]:
    parts = folder.split("/") if folder else []
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
