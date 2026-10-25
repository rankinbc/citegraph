"""The shape a job name must have to be stored: names and locations, never values."""

from __future__ import annotations

import hashlib
import re

_NAME_SHAPED = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}")


def is_name_shaped(value: str) -> bool:
    """A short identifier-like token (`classify_stems`, `Jobs.Run`); free text, URLs and paths are not."""
    return _NAME_SHAPED.fullmatch(value) is not None


def name_digest(value: str) -> str:
    """A one-way digest of a constant's value, stored in its place: a job sent by constant is matched to the handler
    whose name has the same digest, so the value itself (which may be a password, not a job name) is never stored.
    24 hex characters: below the length the redaction entropy check looks at, and far from colliding."""
    return hashlib.blake2b(value.encode("utf-8"), digest_size=12, person=b"citegraph-const").hexdigest()
