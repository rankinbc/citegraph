"""SQLite schema. Names, locations and metadata only: no source text, no config values."""

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  head_sha TEXT NOT NULL, indexed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  path TEXT NOT NULL, lang TEXT NOT NULL, content_hash TEXT NOT NULL, loc INTEGER NOT NULL,
  parse_error INTEGER NOT NULL DEFAULT 0, module TEXT, UNIQUE(repo_id, path));
CREATE TABLE IF NOT EXISTS symbols(
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT NOT NULL,
  line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, visibility TEXT NOT NULL, param_count INTEGER);
CREATE INDEX IF NOT EXISTS ix_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS ix_symbols_qname ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS ix_symbols_file ON symbols(file_id);
CREATE TABLE IF NOT EXISTS refs(
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  from_qualified TEXT NOT NULL, to_name TEXT NOT NULL, kind TEXT NOT NULL, line INTEGER NOT NULL,
  receiver_type TEXT);
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  local_name TEXT NOT NULL, target TEXT NOT NULL, line INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS config_keys(
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  key_path TEXT NOT NULL, key_norm TEXT NOT NULL, line INTEGER NOT NULL, origin TEXT NOT NULL,
  reader_qualified TEXT);
CREATE INDEX IF NOT EXISTS ix_config_norm ON config_keys(key_norm);
CREATE TABLE IF NOT EXISTS entry_points(
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, name TEXT NOT NULL, target TEXT, line INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY, ref_id INTEGER NOT NULL REFERENCES refs(id) ON DELETE CASCADE,
  from_symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  to_symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, rule TEXT NOT NULL, confidence REAL NOT NULL, candidates INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_edges_from ON edges(from_symbol_id);
CREATE INDEX IF NOT EXISTS ix_edges_to ON edges(to_symbol_id);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY, from_qualified TEXT NOT NULL, to_qualified TEXT NOT NULL, kind TEXT NOT NULL,
  note TEXT, source_file TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS index_runs(
  id INTEGER PRIMARY KEY, started TEXT NOT NULL, finished TEXT, repo_shas_json TEXT NOT NULL DEFAULT '{}',
  counts_json TEXT NOT NULL DEFAULT '{}', parse_errors INTEGER NOT NULL DEFAULT 0, leak_scan_clean INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
  name, qualified_name, content='symbols', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
  INSERT INTO symbols_fts(rowid, name, qualified_name) VALUES (new.id, new.name, new.qualified_name);
END;
CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
  INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name)
  VALUES ('delete', old.id, old.name, old.qualified_name);
END;
"""

TABLES = frozenset(
    {
        "repos",
        "files",
        "symbols",
        "refs",
        "imports",
        "config_keys",
        "entry_points",
        "edges",
        "overrides",
        "index_runs",
    }
)
