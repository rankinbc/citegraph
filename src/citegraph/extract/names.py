"""The shape a job name must have to be stored: names and locations, never values."""

from __future__ import annotations

import re

_NAME_SHAPED = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}")


def is_name_shaped(value: str) -> bool:
    """A short identifier-like token (`classify_stems`, `Jobs.Run`); free text, URLs and paths are not."""
    return _NAME_SHAPED.fullmatch(value) is not None
