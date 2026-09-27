"""Shared test helpers. Git calls use a fixed identity so tests run on clean CI machines."""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "citegraph-tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "citegraph-tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}

FIXTURES = Path(__file__).parent / "fixtures"


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def write_files(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows


def commit_all(repo: Path, message: str = "update") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@dataclass(frozen=True)
class SecretCase:
    kind: str
    value: str
    must_match: bool


def load_secret_cases() -> list[SecretCase]:
    data = tomllib.loads((FIXTURES / "secrets.toml").read_text(encoding="utf-8"))
    return [SecretCase(c["kind"], "".join(c["parts"]), bool(c["must_match"])) for c in data["case"]]
