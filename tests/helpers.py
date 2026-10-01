"""Shared test helpers. Git calls use a fixed identity so tests run on clean CI machines."""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from citegraph.extract import EXTRACTORS, stored_module
from citegraph.ingest import language_for
from citegraph.resolve import resolve_all
from citegraph.store import Store

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


def build_store(db_path: Path, repos: dict[str, dict[str, str]], resolve: bool = True) -> Store:
    """Index in-memory repos without git: repo path /virtual/<name>, head sha all zeros."""
    store = Store.open(db_path)
    for name, files in repos.items():
        repo_id = store.upsert_repo(name, f"/virtual/{name}", "0" * 40)
        for rel, text in files.items():
            lang = language_for(rel)
            if lang is None:
                continue
            data = text.encode("utf-8")
            result = EXTRACTORS[lang].extract(rel, data)
            module = stored_module(rel, lang, result)
            store.upsert_file(repo_id, rel, lang, "h", data.count(b"\n") + 1, result, module)
    store.commit()
    if resolve:
        resolve_all(store)
    return store


def edge_set(store: Store) -> set[tuple[str, str, str, str]]:
    sql = (
        "SELECT fs.qualified_name AS f, ts.qualified_name AS t, e.kind, e.rule FROM edges e "
        "JOIN symbols fs ON fs.id = e.from_symbol_id JOIN symbols ts ON ts.id = e.to_symbol_id"
    )
    return {(r["f"], r["t"], r["kind"], r["rule"]) for r in store.conn.execute(sql)}
