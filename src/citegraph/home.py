"""Where citegraph keeps its state. Nothing is ever written inside indexed repos."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def citegraph_home() -> Path:
    override = os.environ.get("CITEGRAPH_HOME")
    return Path(override) if override else Path.home() / ".citegraph"


def index_path(root: Path, name: str | None = None) -> Path:
    resolved = root.resolve()
    key = os.path.normcase(str(resolved)).encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=4).hexdigest()
    return citegraph_home() / "indexes" / f"{name or resolved.name}-{digest}.db"


def audit_dir() -> Path:
    return citegraph_home() / "audit"


def corpus_dir() -> Path:
    return citegraph_home() / "corpus"
