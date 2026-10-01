"""Resolver rules, their confidence, and names too common to resolve by name alone."""

from __future__ import annotations

import builtins
import hashlib
import json

# Bump when resolver logic changes, so existing indexes re-resolve on the next run. The confidence table,
# the candidate cap and the stoplist are part of resolver_fingerprint already.
RESOLVER_VERSION = "1"

CONFIDENCE: dict[str, float] = {
    "direct": 1.0,
    "same_file": 0.95,
    "import_scope": 0.9,
    "repo_unique": 0.7,
    "cross_repo_unique": 0.6,
    "ambiguous": 0.15,
}

RULE_MEANING: dict[str, str] = {
    "direct": "the symbol was parsed directly from this file",
    "same_file": "the name is defined in the same file as the reference",
    "import_scope": "the name was resolved through an import in the referencing file",
    "repo_unique": "only one symbol with this name exists in the repo",
    "cross_repo_unique": "only one symbol with this name exists across all indexed repos",
    "ambiguous": "several symbols share this name; this is one of the candidates",
}

MAX_CANDIDATES = 10


def resolver_fingerprint() -> str:
    """Short hash of everything that decides stored edges besides the files themselves.

    Edges keep the confidence they were resolved with, so an index whose stored fingerprint differs
    is re-resolved even when no file changed. Computed per call so a changed table is always seen.
    """
    payload = json.dumps(
        [RESOLVER_VERSION, sorted(CONFIDENCE.items()), MAX_CANDIDATES, sorted(COMMON_NAMES | PYTHON_BUILTINS)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


COMMON_NAMES = frozenset(
    {
        "get",
        "set",
        "run",
        "init",
        "start",
        "stop",
        "close",
        "open",
        "read",
        "write",
        "update",
        "delete",
        "add",
        "remove",
        "append",
        "extend",
        "pop",
        "push",
        "send",
        "call",
        "execute",
        "process",
        "handle",
        "load",
        "save",
        "create",
        "build",
        "parse",
        "format",
        "render",
        "validate",
        "main",
        "setup",
        "reset",
        "clear",
        "copy",
        "keys",
        "values",
        "items",
        "join",
        "split",
        "strip",
        "replace",
        "encode",
        "decode",
        "emit",
        "next",
        "apply",
        "map",
        "filter",
        "reduce",
        "then",
        "catch",
        "log",
        "debug",
        "info",
        "warning",
        "error",
        "ToString",
        "Equals",
        "GetHashCode",
    }
)

PYTHON_BUILTINS = frozenset(name for name in dir(builtins) if not name.startswith("_"))


def is_stoplisted(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True
    return name in COMMON_NAMES or name in PYTHON_BUILTINS
