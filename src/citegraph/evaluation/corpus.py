"""Pinned public repositories used by the eval. Git calls use fixed argument lists."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel


class CorpusError(Exception):
    """git could not be run, or its output could not be decoded."""


class CorpusRepo(BaseModel):
    name: str
    url: str
    sha: str
    lang: str


def load_corpus(path: Path) -> list[CorpusRepo]:
    data = cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    repos = cast(list[object], data.get("repos", []))
    return [CorpusRepo.model_validate(r) for r in repos]


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", *args], check=check, capture_output=True, text=True)
    except OSError as exc:
        raise CorpusError(f"could not run git ({exc}); install git and make sure it is on PATH") from exc
    except UnicodeDecodeError as exc:
        raise CorpusError("git output could not be decoded as text") from exc


def _git(*args: str) -> str:
    return _run_git(*args).stdout.strip()


def fetch_corpus(repos: list[CorpusRepo], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for repo in repos:
        path = dest / repo.name
        if not (path / ".git").exists():
            _git("clone", "--quiet", "--no-checkout", repo.url, str(path))
        # no check: a missing commit (not yet fetched, or an interrupted clone) exits non-zero
        have_sha = (
            _run_git("-C", str(path), "cat-file", "-e", f"{repo.sha}^{{commit}}", check=False).returncode == 0
        )
        if not have_sha:
            _git("-C", str(path), "fetch", "--quiet", "origin")
        # always checkout: idempotent and cheap, and the only way to be sure the working tree is
        # populated - a --no-checkout clone (fresh or left over from an interrupted run) can leave
        # HEAD already resolved to repo.sha without any files on disk
        _git("-C", str(path), "checkout", "--quiet", "--detach", repo.sha)
        paths.append(path)
    return paths
