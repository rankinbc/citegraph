"""Pinned public repositories used by the eval. Git calls use fixed argument lists."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel


class CorpusRepo(BaseModel):
    name: str
    url: str
    sha: str
    lang: str


def load_corpus(path: Path) -> list[CorpusRepo]:
    data = cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    repos = cast(list[object], data.get("repos", []))
    return [CorpusRepo.model_validate(r) for r in repos]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def fetch_corpus(repos: list[CorpusRepo], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for repo in repos:
        path = dest / repo.name
        fresh = not (path / ".git").exists()
        if fresh:
            _git("clone", "--quiet", "--no-checkout", repo.url, str(path))
        # no check=True: rev-parse HEAD can fail in a fresh --no-checkout clone on some git versions
        current = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        # a fresh --no-checkout clone leaves the working tree empty even when HEAD already
        # resolves to repo.sha (e.g. the pin is the default branch tip), so always check out
        # once right after cloning; on a later call, only re-fetch when the pin moved
        if fresh or current != repo.sha:
            if not fresh:
                _git("-C", str(path), "fetch", "--quiet", "origin")
            _git("-C", str(path), "checkout", "--quiet", "--detach", repo.sha)
        paths.append(path)
    return paths
