"""Secret shapes. Order matters: more specific patterns first (anthropic before openai).

Lookbehinds are used instead of word boundaries so a secret after an underscore
(`notes_AKIA...`) still matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    kind: str
    regex: re.Pattern[str]
    value_group: int = 0  # regex group holding the secret; 0 = whole match


BUILTIN_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*)"),
    ),
    SecretPattern("anthropic-key", re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}")),
    SecretPattern("openai-key", re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    SecretPattern(
        "github-token",
        re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    ),
    SecretPattern("slack-token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9\-]{10,}")),
    SecretPattern("aws-access-key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
    SecretPattern(
        "jwt", re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
    ),
    SecretPattern(
        "connstr-credential",
        re.compile(
            # each captured char must not start an existing `<redacted:...>` marker, so a later
            # scan can't re-match the marker itself, without excluding < > from real values (which
            # would under-redact a value that happens to contain one)
            r"(?i)(?<![A-Za-z0-9])(?:password|pwd|user id|uid|accountkey|sharedaccesskey)\s*=\s*"
            r"((?:(?!<redacted:)[^;\"'\s])+)"
        ),
        value_group=1,
    ),
)

# Long tokens made of base64/url-safe characters. Hex (git shas, hashes) tops out at 4.0 bits
# per character, so a threshold above 4.0 never flags them.
ENTROPY_MIN_LENGTH = 32
ENTROPY_TOKEN = re.compile(rf"[A-Za-z0-9+/=_\-]{{{ENTROPY_MIN_LENGTH},}}")
ENTROPY_THRESHOLD = 4.2

# A token holding "/" is judged whole only when it is base64-shaped; otherwise it is a path or URL and each
# "/"-separated segment is judged on its own. Random base64 changes character class (upper, lower, digit,
# symbol) between about two of every three adjacent characters; path segments are words and identifiers
# and change far less often (sanitizer.is_base64_shaped).
BASE64_MIN_CLASS_CHANGE_RATE = 0.45

# A long identifier (an EF Core migration `20260615134159_AddCoachConversations`, a test method
# `GetUser_Returns404_WhenMissing`) is high-entropy by character count but made of words: split into runs of
# digits, capitalized or lower-case words and upper-case acronyms, it has few number runs, words of word length
# with vowels, short acronyms and at most one stray letter. Random keys almost never do (sanitizer.is_identifier_shaped;
# 0.00%-0.04% of random high-entropy tokens in tests/redact/test_sanitizer.py).
IDENTIFIER_RUN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
IDENTIFIER_MAX_NUMBER_RUNS = 3
IDENTIFIER_MIN_MEAN_WORD = 3.0
IDENTIFIER_MAX_WORD = 16
IDENTIFIER_MAX_ACRONYM = 5
IDENTIFIER_CONSONANT_CLUSTER = re.compile(r"[^aeiouyAEIOUY]{5,}")

# An assignment "=" inside a token holding "/" (`AWS_SECRET_ACCESS_KEY=<value>` is one token): the key and the
# value are judged apart. An "=" before another "=" or at the end of the token is base64 padding and stays.
ASSIGNMENT = re.compile(r"=(?=[^=])")

# A path word: a segment of four or more lower-case letters (`backups`, `tokens`). In a path, path words and
# segments holding "-" or "_" delimit a base64 value (sanitizer._part_spans). A random base64 segment is rarely
# one (26 of its 64 characters are lower-case letters).
PATH_WORD = re.compile(r"[a-z]{4,}")
