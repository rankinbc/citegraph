"""Replace secret-shaped substrings with <redacted:kind>. Used on every write and every response."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from typing import cast

from citegraph.redact.patterns import (
    ASSIGNMENT,
    BASE64_MIN_CLASS_CHANGE_RATE,
    BUILTIN_PATTERNS,
    DATA_URI_CONTEXT,
    DIGEST_BYTES,
    DOTTED_NAME,
    ENCODED_TEXT_MIN_SPACES,
    ENTROPY_EXEMPT_CONTEXT_WINDOW,
    ENTROPY_MIN_LENGTH,
    ENTROPY_THRESHOLD,
    ENTROPY_TOKEN,
    HASH_CONTEXT,
    HASH_KEY,
    HASH_LABELED_TOKEN,
    HEX,
    HEX_OR_UUID,
    ID_KEY,
    IDENTIFIER_CONSONANT_CLUSTER,
    IDENTIFIER_MAX_ACRONYM,
    IDENTIFIER_MAX_NUMBER_RUNS,
    IDENTIFIER_MAX_WORD,
    IDENTIFIER_MIN_MEAN_WORD,
    IDENTIFIER_RUN,
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
            [
                (p.kind, p.regex.pattern, p.regex.flags, p.value_group, p.skip_identifiers)
                for p in BUILTIN_PATTERNS + _extra
            ],
            [
                ENTROPY_TOKEN.pattern,
                ENTROPY_MIN_LENGTH,
                ENTROPY_THRESHOLD,
                BASE64_MIN_CLASS_CHANGE_RATE,
                ASSIGNMENT.pattern,
                PATH_WORD.pattern,
                IDENTIFIER_RUN.pattern,
                IDENTIFIER_MAX_NUMBER_RUNS,
                IDENTIFIER_MIN_MEAN_WORD,
                IDENTIFIER_MAX_WORD,
                IDENTIFIER_MAX_ACRONYM,
                IDENTIFIER_CONSONANT_CLUSTER.pattern,
                ENTROPY_EXEMPT_CONTEXT_WINDOW,
                DATA_URI_CONTEXT.pattern,
                HASH_CONTEXT.pattern,
                HASH_LABELED_TOKEN.pattern,
                HASH_KEY.pattern,
                ID_KEY.pattern,
                HEX_OR_UUID.pattern,
                HEX.pattern,
                DIGEST_BYTES,
                DOTTED_NAME.pattern,
                ENCODED_TEXT_MIN_SPACES,
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
        and not is_identifier_shaped(token)
    )


def _word_shaped(word: str) -> bool:
    if len(word) == 1:
        return True
    if word.isupper():
        return len(word) <= IDENTIFIER_MAX_ACRONYM
    if len(word) > IDENTIFIER_MAX_WORD or IDENTIFIER_CONSONANT_CLUSTER.search(word):
        return False
    return len(word) < 3 or any(ch in "aeiouyAEIOUY" for ch in word)


def is_identifier_shaped(token: str) -> bool:
    """True when a token of letters, digits, "_" and "-" reads as a name made of words rather than a random value."""
    if any(not (ch.isascii() and (ch.isalnum() or ch in "_-")) for ch in token):
        return False
    runs = IDENTIFIER_RUN.findall(token)
    words = [run for run in runs if not run.isdigit()]
    if not words or len(runs) - len(words) > IDENTIFIER_MAX_NUMBER_RUNS:
        return False
    if sum(len(word) == 1 for word in words) > 1:
        return False
    if sum(map(len, words)) / len(words) < IDENTIFIER_MIN_MEAN_WORD:
        return False
    return all(_word_shaped(word) for word in words)


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


def is_encoded_text(token: str) -> bool:
    """True when a base64 token decodes to printable UTF-8 text with spaces (patterns.ENCODED_TEXT_MIN_SPACES)."""
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return decoded.count(" ") >= ENCODED_TEXT_MIN_SPACES and all(
        ch.isprintable() or ch in "\t\r\n" for ch in decoded
    )


def is_digest_shaped(value: str) -> bool:
    """True when `value` is hex or standard base64 of a hash-sized value (patterns.DIGEST_BYTES)."""
    if HEX.fullmatch(value):
        return len(value) % 2 == 0 and len(value) // 2 in DIGEST_BYTES
    try:
        return len(base64.b64decode(value, validate=True)) in DIGEST_BYTES
    except binascii.Error:
        return False


def _glued_value_is_exempt(token: str) -> bool:
    """`KEY=value` in one token: a digest under a hash-named key, or a hex or UUID value under an id-named key."""
    key, sep, value = token.partition("=")
    if not sep or not key or not value or value.startswith("="):
        return False
    if HASH_KEY.search(key):
        return is_digest_shaped(value)
    return bool(ID_KEY.search(key)) and bool(HEX_OR_UUID.fullmatch(value))


def is_entropy_exempt(text: str, start: int, token: str) -> bool:
    """True when the ENTROPY_TOKEN match `token` at `start` is encoded data, a digest or an id by its context
    (see patterns.DATA_URI_CONTEXT), or encoded prose (`is_encoded_text`): the entropy rule skips it."""
    context = text[max(0, start - ENTROPY_EXEMPT_CONTEXT_WINDOW) : start]
    if DATA_URI_CONTEXT.search(context):
        return True
    if HASH_CONTEXT.search(context) and is_digest_shaped(token):
        return True
    if (label := HASH_LABELED_TOKEN.match(token)) and is_digest_shaped(token[label.end() :]):
        return True
    return _glued_value_is_exempt(token) or is_encoded_text(token)


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
    """Pattern hits in pattern order, then high-entropy spans; a span overlapping an earlier hit is dropped.

    Kept hits never overlap, so their starts and ends are both sorted: one bisect finds the only kept hit a new
    span can overlap, and a file with thousands of hits is not quadratic.
    """
    starts: list[int] = []
    ends: list[int] = []
    hits: list[SecretHit] = []

    def keep(kind: str, start: int, end: int) -> None:
        i = bisect_right(ends, start)  # first kept hit ending after `start`
        if i < len(starts) and starts[i] < end:
            return
        starts.insert(i, start)
        ends.insert(i, end)
        hits.insert(i, SecretHit(kind, start, end))

    for pattern in BUILTIN_PATTERNS + _extra:
        for match in pattern.regex.finditer(text):
            value = match.group(pattern.value_group)
            if pattern.skip_identifiers and (is_identifier_shaped(value) or DOTTED_NAME.fullmatch(value)):
                continue
            keep(pattern.kind, *match.span(pattern.value_group))
    for match in ENTROPY_TOKEN.finditer(text):
        token = match.group(0)
        if is_entropy_exempt(text, match.start(), token):
            continue
        for start, end in _entropy_spans(token, match.start()):
            keep("high-entropy", start, end)
    return hits


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


_MARKER = re.compile(r"<redacted:([a-z-]+)>")


def sanitize_path(path: str) -> str:
    """`sanitize`, with each redaction marker tagged by a short hash of the whole path (`<redacted:kind~1a2b3c4d>`),
    so two files whose paths redact alike keep their own rows. Eight hex digits cannot be inverted to a redacted
    value of 32 or more random characters, and a name a redaction pattern caught by mistake is not a secret."""
    sanitized = sanitize(path)
    if sanitized == path:
        return path
    tag = hashlib.blake2b(path.encode("utf-8"), digest_size=4).hexdigest()
    return _MARKER.sub(lambda m: f"<redacted:{m.group(1)}~{tag}>", sanitized)


def sanitize_obj(obj: object) -> object:
    if isinstance(obj, str):
        return sanitize(obj)
    if isinstance(obj, dict):
        items = cast(dict[object, object], obj).items()
        return {(sanitize(k) if isinstance(k, str) else k): sanitize_obj(v) for k, v in items}
    if isinstance(obj, list | tuple):
        return [sanitize_obj(v) for v in cast(list[object], obj)]
    return obj
