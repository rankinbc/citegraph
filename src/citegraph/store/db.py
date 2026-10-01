"""The index database. `_write` is the only method that modifies data, and it sanitizes every string."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from citegraph.keys import normalize_key
from citegraph.models import ExtractResult, ToolError
from citegraph.redact import sanitize
from citegraph.store.schema import SCHEMA, SCHEMA_VERSION, TABLES


@dataclass(frozen=True)
class EdgeRow:
    ref_id: int
    from_symbol_id: int
    to_symbol_id: int
    kind: str
    rule: str
    confidence: float
    candidates: int


class StoredFile(NamedTuple):
    id: int
    content_hash: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, path: Path) -> Store:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # zero deleted content: rows dropped when the redaction rules change must not survive in freed pages
        conn.execute("PRAGMA secure_delete=ON")
        conn.executescript(SCHEMA)
        store = cls(conn)
        store._migrate()
        store._write(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
        )
        store.commit()
        return store

    @classmethod
    def open_read_only(cls, path: Path) -> Store:
        if not path.exists():
            raise ToolError(
                "not_indexed", f"no index found ({path.name})", hint="run `citegraph index <root>` first"
            )
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return cls(conn)

    def _migrate(self) -> None:
        """Add columns that a newer schema version introduced to an index written by an older release."""
        columns = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(refs)")}
        if "receiver_type" not in columns:
            self._write("ALTER TABLE refs ADD COLUMN receiver_type TEXT")

    def _write(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        clean = [sanitize(p) if isinstance(p, str) else p for p in params]
        return self.conn.execute(sql, clean)

    def upsert_repo(self, name: str, path: str, head_sha: str) -> int:
        self._write(
            "INSERT INTO repos(name, path, head_sha, indexed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET path=excluded.path, head_sha=excluded.head_sha, "
            "indexed_at=excluded.indexed_at",
            (name, path, head_sha, _now()),
        )
        row = self.conn.execute("SELECT id FROM repos WHERE name = ?", (sanitize(name),)).fetchone()
        return int(row["id"])

    def stored_files(self, repo_id: int) -> dict[str, StoredFile]:
        """Stored path -> row id and content hash.

        The paths were sanitized under the rules in force when they were written. Compare them with this run's
        keys, but never pass one back as a write parameter: `_write` sanitizes it again under the current rules
        and may turn it into a different string, so a lookup by it can miss the stored row.
        """
        rows = self.conn.execute("SELECT id, path, content_hash FROM files WHERE repo_id = ?", (repo_id,))
        return {str(r["path"]): StoredFile(int(r["id"]), str(r["content_hash"])) for r in rows}

    def delete_file(self, repo_id: int, rel_path: str) -> None:
        self._write("DELETE FROM files WHERE repo_id = ? AND path = ?", (repo_id, rel_path))

    def delete_file_id(self, file_id: int) -> None:
        self._write("DELETE FROM files WHERE id = ?", (file_id,))

    def delete_repo_files(self, repo_id: int) -> None:
        self._write("DELETE FROM files WHERE repo_id = ?", (repo_id,))

    def upsert_file(
        self,
        repo_id: int,
        rel_path: str,
        lang: str,
        content_hash: str,
        loc: int,
        result: ExtractResult,
        module: str | None,
    ) -> int:
        self.delete_file(repo_id, rel_path)
        cur = self._write(
            "INSERT INTO files(repo_id, path, lang, content_hash, loc, parse_error, module) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (repo_id, rel_path, lang, content_hash, loc, int(result.parse_error), module),
        )
        file_id = int(cur.lastrowid or 0)
        for s in result.symbols:
            self._write(
                "INSERT INTO symbols(file_id, kind, name, qualified_name, line_start, line_end, visibility, "
                "param_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    file_id,
                    s.kind,
                    s.name,
                    s.qualified_name,
                    s.line_start,
                    s.line_end,
                    s.visibility,
                    s.param_count,
                ),
            )
        for r in result.references:
            self._write(
                "INSERT INTO refs(file_id, from_qualified, to_name, kind, line, receiver_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, r.from_qualified, r.to_name, r.kind, r.line, r.receiver_type),
            )
        for i in result.imports:
            self._write(
                "INSERT INTO imports(file_id, local_name, target, line) VALUES (?, ?, ?, ?)",
                (file_id, i.local_name, i.target, i.line),
            )
        for k in result.config_keys:
            self._write(
                "INSERT INTO config_keys(file_id, key_path, key_norm, line, origin, reader_qualified) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    file_id,
                    k.key_path,
                    normalize_key(sanitize(k.key_path)),
                    k.line,
                    k.origin,
                    k.reader_qualified,
                ),
            )
        for e in result.entry_points:
            self._write(
                "INSERT INTO entry_points(file_id, kind, name, target, line) VALUES (?, ?, ?, ?, ?)",
                (file_id, e.kind, e.name, e.target, e.line),
            )
        return file_id

    def delete_repos_not_in(self, names: Iterable[str]) -> int:
        keep = [sanitize(n) for n in names]
        if not keep:
            return self._write("DELETE FROM repos").rowcount
        placeholders = ",".join("?" * len(keep))
        return self._write(f"DELETE FROM repos WHERE name NOT IN ({placeholders})", keep).rowcount

    def replace_edges(self, rows: Iterable[EdgeRow]) -> int:
        self._write("DELETE FROM edges")
        count = 0
        for r in rows:
            self._write(
                "INSERT INTO edges(ref_id, from_symbol_id, to_symbol_id, kind, rule, confidence, candidates) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r.ref_id, r.from_symbol_id, r.to_symbol_id, r.kind, r.rule, r.confidence, r.candidates),
            )
            count += 1
        return count

    def start_run(self) -> int:
        return int(self._write("INSERT INTO index_runs(started) VALUES (?)", (_now(),)).lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        repo_shas: dict[str, str],
        counts: dict[str, int],
        parse_errors: int,
        leak_scan_clean: bool,
    ) -> None:
        self._write(
            "UPDATE index_runs SET finished = ?, repo_shas_json = ?, counts_json = ?, parse_errors = ?, "
            "leak_scan_clean = ? WHERE id = ?",
            (_now(), json.dumps(repo_shas), json.dumps(counts), parse_errors, int(leak_scan_clean), run_id),
        )

    def optimize_fts(self) -> None:
        """Merge the symbol search index into one segment.

        A deleted symbol is only recorded as a delete marker; its trigrams stay in older segments until they are
        merged, so a run that deleted rows calls this to drop them.
        """
        self._write("INSERT INTO symbols_fts(symbols_fts) VALUES('optimize')")

    def vacuum(self) -> None:
        """Rebuild the file, so free pages and free space in pages hold no deleted content.

        `secure_delete` only zeroes what is deleted while it is on; an index written before it was turned on keeps
        older deleted content until this runs. VACUUM cannot run inside a transaction, so this commits first.
        """
        self.commit()
        self._write("VACUUM")

    def set_meta(self, key: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (sanitize(key),)).fetchone()
        return None if row is None else str(row["value"])

    def count(self, table: str) -> int:
        if table not in TABLES:
            raise ValueError(f"unknown table {table!r}")
        return int(self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def commit(self) -> None:
        self.conn.commit()

    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        self.conn.close()
