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
    # a value made of words (sanitizer.is_identifier_shaped) or a dotted name (DOTTED_NAME) is not a hit
    skip_identifiers: bool = False


# A qualified name in code (`ed25519.Ed25519PrivateKey`, `settings.DB_PASSWORD2`): never a secret value.
DOTTED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


# A key ending in a secret word (`password`, `DB_PASSWORD`, `"apiToken"`, `SECRET_KEY`, `client_secret`), then an
# assignment on the same line (`=`, `:`, `:=`, `=>`, not `==`). Only the end of the key is matched: matching the
# whole key from its start would retry the secret words at every character of every word. The lookahead on the
# first letters makes the scan about three times faster.
SECRET_KEY_PREFIX = (
    r"(?i)(?=[acempst])(?:pass(?:word|wd|phrase)|secret|token|credentials?|(?:api|access|secret|private|signing|encryption|"
    r"master|client|auth)[_\-]?key)(?<!publickeytoken)[\"']?[ \t]*(?::=|=>|[:=])(?!=)[ \t]*"
)
PLACEHOLDER = r"[$<{%*]|(?i:(?:your|my)[_\-\s]|change[_\-]?me|example|placeholder|dummy|x{4}|redacted)"

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
        "gitlab-token", re.compile(r"(?<![A-Za-z0-9])gl(?:pat|dt|rt|ptt|soat)-[A-Za-z0-9_\-]{20,}")
    ),
    SecretPattern(
        "shopify-token", re.compile(r"(?<![A-Za-z0-9])shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}(?![A-Za-z0-9])")
    ),
    SecretPattern("vault-token", re.compile(r"(?<![A-Za-z0-9])hv[sbr]\.[A-Za-z0-9_\-]{24,}")),
    SecretPattern(
        # user id (base64) . timestamp (6) . HMAC (27+); a digit in the first and last part keeps dotted
        # names (`Company.Common.Something`) out
        "discord-token",
        re.compile(
            r"(?<![A-Za-z0-9_.\-])(?=[A-Za-z]{0,27}[0-9])[A-Za-z0-9]{24,28}\.[A-Za-z0-9_\-]{6}"
            r"\.(?=[A-Za-z_\-]{0,37}[0-9])[A-Za-z0-9_\-]{27,38}(?![A-Za-z0-9_\-])"
        ),
    ),
    SecretPattern(
        "jwt", re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
    ),
    SecretPattern(
        # the password in `scheme://user:<password>@host`; not `user@host`, not a template placeholder
        # (`${PASSWORD}`, `<password>`, `{password}`, `%(password)s`) and not the word itself (`user:pass@`)
        "url-credential",
        re.compile(
            r"(?i)(?<![A-Za-z0-9+.\-])[a-z][a-z0-9+.\-]*://[^\s/?#@:'\"<>]*:"
            r"(?![$<{*]|%\(|(?:pass(?:word)?|secret|pwd|x+)@)([^\s/?#@'\"<>]+)@"
        ),
        value_group=1,
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
    # A value assigned to a secret-named key in YAML, JSON, TOML, .env or code (`password: x`, `"apiToken": "x"`,
    # `SECRET_KEY = 'x'`), whatever its shape: hex and short values are not high-entropy. A value needs 8
    # characters and a letter. An unquoted one also needs a digit, must end the value and must not read as an
    # identifier, so names, numbers and expressions in code (`password: str`, `private_key: Ed25519PrivateKey`,
    # `NO_TOKEN = 16777216`, `token = get_token()`) are not values. Placeholders (`${X}`, `<x>`, `{{ x }}`,
    # `%(x)s`, `your-token`, `changeme`) and redaction markers are not secrets.
    SecretPattern(
        "secret-assignment",
        re.compile(
            rf"{SECRET_KEY_PREFIX}([\"'])(?!{PLACEHOLDER})(?=[^\s\"']*[A-Za-z])"
            rf"((?:(?!<redacted:)[^\s\"']){{8,}})(?=\1)"
        ),
        value_group=2,
    ),
    SecretPattern(
        "secret-assignment",
        re.compile(
            rf"{SECRET_KEY_PREFIX}(?![\"'(\[])(?!{PLACEHOLDER})(?!0x[0-9a-f]+(?:[\s,;)}}\]]|$))"
            rf"(?=[^\s\"',;#(){{}}\[\]<>`]*[0-9])(?=[^\s\"',;#(){{}}\[\]<>`]*[A-Za-z])"
            rf"((?:(?!<redacted:)[^\s\"',;#(){{}}\[\]<>`]){{8,}})(?=[\s,;)}}\]]|$)"
        ),
        value_group=1,
        skip_identifiers=True,
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

# Contexts in which a long token is encoded data, a hash or an id, never a secret: the entropy rule skips it (named
# patterns still apply; sanitizer.is_entropy_exempt).
# - The payload of a `data:` URI (`data:image/png;base64,...`), whatever its length.
# - A digest: hex or standard base64 of a hash-sized value (DIGEST_BYTES) after a hash label (`h1:` in go.sum,
#   `sha256:`), as an SRI or lockfile integrity value (`sha512-...`), or assigned to a hash-named key
#   (`"contentHash": "...`, `COMMIT_SHA=...`). A random value of another length under such a key is still judged.
# - A hex or UUID value glued to an id-named key (`REQUEST_ID=<uuid>`); unglued, hex and UUIDs are never high-entropy.
ENTROPY_EXEMPT_CONTEXT_WINDOW = 80
DATA_URI_CONTEXT = re.compile(r"(?i)data:[a-z0-9.+/\-]*(?:;[a-z0-9.+=\-]*)*;base64,$")
HASH_KEY_WORD = r"(?:sha[0-9]*|hash|digest|checksum|integrity|commit|revision|etag|fingerprint)"
HASH_CONTEXT = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9])(?:h1|sha[0-9]+|md5|blake2[bs]?):"
    rf"|(?<![A-Za-z0-9_.\-])[\"']?[A-Za-z0-9_.\-]*?{HASH_KEY_WORD}[\"']?[ \t]*(?::=|=>|[:=])[ \t]*[\"']?)$"
)
HASH_LABELED_TOKEN = re.compile(r"(?i)(?:sha(?:1|224|256|384|512)|md5)-")
HASH_KEY = re.compile(rf"(?i){HASH_KEY_WORD}$")
ID_KEY = re.compile(r"(?i)(?:id|uuid|guid)$")
HEX_OR_UUID = re.compile(r"[0-9a-fA-F]+(?:-[0-9a-fA-F]+)*")
HEX = re.compile(r"[0-9a-fA-F]+")
DIGEST_BYTES = (16, 20, 28, 32, 48, 64)  # md5, sha1, sha224, sha256, sha384, sha512

# A base64 token that decodes to text with this many spaces is encoded prose (a test fixture), not a key: random
# bytes almost never decode to printable UTF-8, and encoded credentials (`user:password`) hold no spaces.
ENCODED_TEXT_MIN_SPACES = 2
