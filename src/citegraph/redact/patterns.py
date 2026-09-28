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
            # value excludes < > too, so a `<redacted:kind>` marker never re-matches on a later scan
            r"(?i)(?<![A-Za-z0-9])(?:password|pwd|user id|uid|accountkey|sharedaccesskey)\s*=\s*([^;\"'\s<>]+)"
        ),
        value_group=1,
    ),
)

# Long tokens made of base64/url-safe characters. Hex (git shas, hashes) tops out at 4.0 bits
# per character, so a threshold above 4.0 never flags them.
ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
ENTROPY_THRESHOLD = 4.2
