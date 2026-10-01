"""Where citegraph keeps its state. Nothing is ever written inside indexed repos."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

INDEX_NAME = re.compile(r"[A-Za-z0-9._-]+")


def citegraph_home() -> Path:
    override = os.environ.get("CITEGRAPH_HOME")
    return Path(override) if override else Path.home() / ".citegraph"


def check_index_name(name: str) -> str:
    """An index name becomes part of a file name under the citegraph home, so no separators, `.` or `..`."""
    if not INDEX_NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError(f"invalid index name {name!r}: use only letters, digits, '.', '_' and '-'")
    return name


def index_path(root: Path, name: str | None = None) -> Path:
    resolved = root.resolve()
    key = os.path.normcase(str(resolved)).encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=4).hexdigest()
    stem = resolved.name if name is None else check_index_name(name)
    return citegraph_home() / "indexes" / f"{stem}-{digest}.db"


def audit_dir() -> Path:
    return citegraph_home() / "audit"


def corpus_dir() -> Path:
    return citegraph_home() / "corpus"
