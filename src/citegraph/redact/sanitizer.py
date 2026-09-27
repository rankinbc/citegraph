"""Replace secret-shaped substrings with <redacted:kind>. Used on every write and every response."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from citegraph.redact.patterns import BUILTIN_PATTERNS, ENTROPY_THRESHOLD, ENTROPY_TOKEN, SecretPattern

_extra: tuple[SecretPattern, ...] = ()


@dataclass(frozen=True)
class SecretHit:
    kind: str
    start: int
    end: int


def configure_extra_patterns(patterns: list[str]) -> None:
    global _extra
    _extra = tuple(SecretPattern("custom", re.compile(p)) for p in patterns)
    sanitize.cache_clear()


def shannon_entropy(token: str) -> float:
    counts = Counter(token)
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_high_entropy(token: str) -> bool:
    return (
        any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
        and shannon_entropy(token) > ENTROPY_THRESHOLD
    )


def find_secrets(text: str) -> list[SecretHit]:
    hits: list[SecretHit] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < h.end and h.start < end for h in hits)

    for pattern in BUILTIN_PATTERNS + _extra:
        for match in pattern.regex.finditer(text):
            start, end = match.span(pattern.value_group)
            if not overlaps(start, end):
                hits.append(SecretHit(pattern.kind, start, end))
    for match in ENTROPY_TOKEN.finditer(text):
        start, end = match.span()
        if is_high_entropy(match.group(0)) and not overlaps(start, end):
            hits.append(SecretHit("high-entropy", start, end))
    return sorted(hits, key=lambda h: h.start)


@lru_cache(maxsize=65536)
def sanitize(text: str) -> str:
    hits = find_secrets(text)
    if not hits:
        return text
    out: list[str] = []
    pos = 0
    for hit in hits:
        out.append(text[pos : hit.start])
        out.append(f"<redacted:{hit.kind}>")
        pos = hit.end
    out.append(text[pos:])
    return "".join(out)


def sanitize_obj(obj: object) -> object:
    if isinstance(obj, str):
        return sanitize(obj)
    if isinstance(obj, dict):
        items = cast(dict[object, object], obj).items()
        return {(sanitize(k) if isinstance(k, str) else k): sanitize_obj(v) for k, v in items}
    if isinstance(obj, list | tuple):
        return [sanitize_obj(v) for v in cast(list[object], obj)]
    return obj
