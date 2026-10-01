"""Replace secret-shaped substrings with <redacted:kind>. Used on every write and every response."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from typing import cast

from citegraph.redact.patterns import (
    ASSIGNMENT,
    BASE64_MIN_CLASS_CHANGE_RATE,
    BUILTIN_PATTERNS,
    ENTROPY_MIN_LENGTH,
    ENTROPY_THRESHOLD,
    ENTROPY_TOKEN,
    PATH_WORD,
    SecretPattern,
)

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


def redaction_fingerprint() -> str:
    """Short hash of every pattern and parameter `sanitize` uses, including the configured extra patterns."""
    payload = json.dumps(
        [
            [(p.kind, p.regex.pattern, p.regex.flags, p.value_group) for p in BUILTIN_PATTERNS + _extra],
            [
                ENTROPY_TOKEN.pattern,
                ENTROPY_MIN_LENGTH,
                ENTROPY_THRESHOLD,
                BASE64_MIN_CLASS_CHANGE_RATE,
                ASSIGNMENT.pattern,
                PATH_WORD.pattern,
            ],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


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


def _char_class(ch: str) -> str:
    if ch.isupper():
        return "upper"
    if ch.islower():
        return "lower"
    return "digit" if ch.isdigit() else "symbol"


def is_base64_shaped(token: str) -> bool:
    """True when a token holding "/" reads as one base64 value rather than a path or URL.

    - Standard base64 uses "+" and "/", the url-safe alphabet "-" and "_" in their place, so a token that
      mixes "/" with "-" or "_" is not base64.
    - Random base64 of 32 or more characters almost always has upper case, lower case and digits; hex and
      lower-case folder names (hash directories, temp folders) do not.
    - Random base64 changes character class between about two of every three adjacent characters
      (0.65 expected); words and identifiers between path separators change far less often.
    """
    if "-" in token or "_" in token:
        return False
    if not (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
    ):
        return False
    pairs = [pair for segment in token.split("/") for pair in pairwise(segment)]
    changes = sum(_char_class(a) != _char_class(b) for a, b in pairs)
    return bool(pairs) and changes / len(pairs) >= BASE64_MIN_CLASS_CHANGE_RATE


def _entropy_spans(token: str, start: int) -> list[tuple[int, int]]:
    """High-entropy spans in one ENTROPY_TOKEN match starting at `start`.

    A token without "/" is judged whole. A token holding "/" is first split at each assignment "=", so the
    key name of a .env line never decides how its value is judged; each part is then judged by `_part_spans`.
    """
    if "/" not in token:
        return [(start, start + len(token))] if is_high_entropy(token) else []
    spans: list[tuple[int, int]] = []
    for part in ASSIGNMENT.split(token):
        spans.extend(_part_spans(part, start))
        start += len(part) + 1
    return spans


def _part_spans(part: str, start: int) -> list[tuple[int, int]]:
    """A part without "/", or a base64-shaped one, is judged whole. Any other part is a path or URL.

    Standard base64 never holds "-" or "_", and a random value rarely has a path word as a segment, so in a path a
    base64 value lies inside a block of consecutive segments that are neither: segments holding "-" or "_" and
    path words delimit the blocks (`my-app/<value>/x_y`, `v1/tokens/<value>/revoke_all`). Each block is judged by
    `_block_spans`; a delimiting segment is judged on its own.
    """
    if is_high_entropy(part) and ("/" not in part or is_base64_shaped(part)):
        return [(start, start + len(part))]
    spans: list[tuple[int, int]] = []
    block: list[tuple[int, str]] = []  # (offset, segment)
    for segment in part.split("/"):
        if "-" in segment or "_" in segment or PATH_WORD.fullmatch(segment):
            spans += _block_spans(block) + _segment_spans(start, segment)
            block = []
        else:
            block.append((start, segment))
        start += len(segment) + 1
    return spans + _block_spans(block)


def _segment_spans(start: int, segment: str) -> list[tuple[int, int]]:
    if len(segment) >= ENTROPY_MIN_LENGTH and is_high_entropy(segment):
        return [(start, start + len(segment))]
    return []


def _block_spans(block: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """A block is judged whole when it is at least 32 characters, high-entropy and base64-shaped, and its longest
    segment mixes upper- and lower-case letters: random base64 almost always does, while a hex hash or a
    lower-case temporary folder name does not, even when a neighbor such as macOS's `T` brings an upper-case
    letter into the block. Otherwise each segment is judged on its own, so digits in a folder or file name never
    redact the path while a long random segment is still caught.
    """
    text = "/".join(segment for _, segment in block)
    longest = max((segment for _, segment in block), key=len, default="")
    if (
        len(text) >= ENTROPY_MIN_LENGTH
        and is_high_entropy(text)
        and is_base64_shaped(text)
        and any(c.isupper() for c in longest)
        and any(c.islower() for c in longest)
    ):
        (first, _), (last, segment) = block[0], block[-1]
        return [(first, last + len(segment))]
    return [span for offset, segment in block for span in _segment_spans(offset, segment)]


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
        for start, end in _entropy_spans(match.group(0), match.start()):
            if not overlaps(start, end):
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
