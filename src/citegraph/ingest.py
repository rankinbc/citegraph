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


# An indexed repo's own .git/config is untrusted input. Of the settings that make git run a program, only
# core.fsmonitor is reachable from the local reads citegraph makes: `ls-files` loads the index and runs the
# fsmonitor hook to ask what changed (`rev-parse` does not today, but gets the same flag). Hooks, filters,
# diff.external, the pager (stdout is never a terminal here) and the ssh/credential/protocol settings are not
# consulted by these commands, so they are left alone. The system and global config are the user's own and are
# kept: they may carry safe.directory and similar settings that normal use needs, so GIT_CONFIG_NOSYSTEM is not set.
GIT_SAFE_CONFIG = ("-c", "core.fsmonitor=false")
# repo discovery: how many folders below the root to look, and folders not searched for repos (dot-folders such
# as .git and .venv are skipped too); a folder that is itself a repo is still found under any name
MAX_DISCOVERY_DEPTH = 4
SKIP_DIRS = frozenset({"node_modules", "venv", "__pycache__", "site-packages", "bin", "obj"})


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *GIT_SAFE_CONFIG, "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        raise IngestError(f"git {args[0]} failed in {repo.name}: {exc.stderr.strip()}") from exc
    except OSError as exc:
        raise IngestError(f"could not run git ({exc}); install git and make sure it is on PATH") from exc
    except UnicodeDecodeError as exc:
        raise IngestError(f"git {args[0]} in {repo.name} printed output that is not valid UTF-8") from exc
    return out.stdout


def discover_repos(root: Path) -> list[Path]:
    """The root if it is a repo, else every repo up to MAX_DISCOVERY_DEPTH folders below it, sorted by path.

    A found repo is not searched further (its nested folders belong to it), and symlinked or junctioned
    folders are never followed.
    """
    root = root.resolve()
    if (root / ".git").exists():
        return [root]
    found: list[Path] = []
    _find_repos(root, 1, found)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def _find_repos(folder: Path, depth: int, found: list[Path]) -> None:
    try:
        children = list(folder.iterdir())
    except OSError:  # unreadable folder: nothing to index there
        return
    for child in children:
        if _is_link(child) or not child.is_dir():
            continue
        if (child / ".git").exists():
            found.append(child)  # not searched further: its folders, nested checkouts included, belong to it
        elif depth < MAX_DISCOVERY_DEPTH and child.name not in SKIP_DIRS and not child.name.startswith("."):
            _find_repos(child, depth + 1, found)


def _is_link(path: Path) -> bool:
    # is_junction: an NTFS junction is not a symlink to Python but leads outside the tree just the same
    return path.is_symlink() or path.is_junction()


def repo_name(root: Path, repo: Path) -> str:
    """A discovered repo's name: its path below the root (a direct child's is its folder name), or the root's."""
    root = root.resolve()
    return repo.name if repo == root else repo.relative_to(root).as_posix()


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def tracked_files(repo: Path) -> list[str]:
    return [p for p in _git(repo, "ls-files", "-z").split("\0") if p]


def language_for(rel_path: str) -> str | None:
    name = rel_path.rsplit("/", 1)[-1]
    if name == "citegraph.overrides.yaml":
        return "overrides"
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


def inside_repo(path: Path, root: Path) -> bool:
    """Whether path is a regular file, not itself a link, whose real location is inside root.

    git never lists files below a tracked symlink, but a working tree can still lead outside the repo: a tracked
    symlink to a file, or a folder replaced by a symlink or junction after checkout.
    """
    try:
        return not _is_link(path) and path.is_file() and path.resolve().is_relative_to(root)
    except OSError:
        return False


def scan_repo(repo: Path, config: CitegraphConfig, name: str | None = None) -> RepoInfo:
    try:
        sha = head_sha(repo)
    except IngestError as exc:
        raise IngestError(f"{repo.name}: no commits yet, or HEAD is unreadable: {exc}") from exc
    real_root = repo.resolve()
    files: list[RepoFile] = []
    for rel in tracked_files(repo):
        lang = language_for(rel)
        if lang is None or (lang not in ("config", "overrides") and lang not in config.languages):
            continue
        if not _selected(rel, config):
            continue
        path = repo / rel
        if inside_repo(path, real_root):
            files.append(RepoFile(rel_path=rel, lang=lang, size=path.stat().st_size))
    return RepoInfo(name=name or repo.name, path=repo, head_sha=sha, files=files)


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


def scan_projects(
    repo: Path, config: CitegraphConfig, name: str | None = None
) -> tuple[list[RepoInfo], list[str]]:
    """The repository as logical repos, one per project folder plus one for the rest, and any warnings.

    A project's files are re-rooted at its folder, so module names and evidence paths start there.
    """
    info = scan_repo(repo, config, name)
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
                warnings.append(f"{info.name}: project folder {root!r} holds no tracked files")
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
