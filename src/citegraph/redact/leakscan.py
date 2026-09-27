"""Scan files for secret-shaped values. Reports locations only, never the value.

SQLite files are scanned cell by cell: SQLite stores adjacent column values back to back, so a raw byte scan
would join two harmless values into one long mixed token and report a false high-entropy hit.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from citegraph.redact.sanitizer import find_secrets

SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def scan_text(text: str, path: str) -> list[Finding]:
    return [Finding(path, text.count("\n", 0, hit.start) + 1, hit.kind) for hit in find_secrets(text)]


def scan_sqlite(path: Path) -> list[Finding]:
    """Every TEXT cell of every ordinary table, scanned separately. `line` is the rowid."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'"
            )
        ]
        findings: list[Finding] = []
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            try:
                rows = conn.execute(f"SELECT rowid, * FROM {quoted}")
            except sqlite3.OperationalError:  # WITHOUT ROWID tables
                rows = conn.execute(f"SELECT 0, * FROM {quoted}")
            for row in rows:
                for value in row[1:]:
                    if isinstance(value, str):
                        findings.extend(
                            Finding(f"{path}#{table}", int(row[0]), h.kind) for h in find_secrets(value)
                        )
        return findings
    finally:
        conn.close()


def _iter_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from (f for f in sorted(path.rglob("*")) if f.is_file() and ".git" not in f.parts)
        elif path.is_file():
            yield path


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for file in _iter_files(paths):
        data = file.read_bytes()
        if data.startswith(SQLITE_MAGIC):
            findings.extend(scan_sqlite(file))
        else:
            # latin-1 maps every byte to one character, so any binary file scans without errors
            findings.extend(scan_text(data.decode("latin-1"), str(file)))
    return findings
