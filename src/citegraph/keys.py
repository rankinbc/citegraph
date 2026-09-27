"""Config key normalization so equivalent spellings link to one key."""

from __future__ import annotations


def normalize_key(key_path: str) -> str:
    """`A:B` (.NET), `A__B` (env var) and `a.b` (YAML path) all become `a:b`."""
    return key_path.strip().lower().replace("__", ":").replace(".", ":")
