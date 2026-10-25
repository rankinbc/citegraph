# Changelog

## 0.2.0 (Unreleased)

First tagged release, and the first published as a Claude Code plugin.

### Added

- Claude Code plugin: `.claude-plugin/plugin.json` runs the MCP server with uv from the plugin's pinned copy and
  serves the folder Claude Code was started in; `/citegraph:index` builds or updates the index. Install with
  `/plugin marketplace add rankinbc/claude-plugins` and `/plugin install citegraph@rankinbc`.
- `from m import *` is recorded and followed, for re-exports and for bare calls (`__all__` is not read).
- `actor_name=CONSTANT` uses a module-level string constant instead of falling back to the function's name.
- Repo discovery walks up to four levels below the root; deeper repos are named by their relative path.
- Leak scan: GitLab, Shopify, Vault and Discord token shapes, passwords in URL userinfo, values assigned to
  secret-named keys in YAML, JSON, .env and code, and UTF-16 files.

### Security

- A repo's own `.git/config` could make indexing run a program (`core.fsmonitor`). Every git call now turns it off.
- C# `const string` values were stored in plain text, so a secret in one sat in the index. Only a one-way digest is
  stored now. Config keys read in code are stored only when they are shaped like names.
- Files reached through a symlink or junction that points outside the repo were indexed. They are skipped.
- A small YAML file with nested aliases could hang indexing. Anchors are walked once, and a file needing more than
  100,000 key/value pairs is skipped as a parse error.
- An older citegraph rewrote an index built by a newer one. It now refuses, naming both versions.

### Fixed

- JSON config keys: linear-time line lookup, and correct lines for non-ASCII, escaped and same-named keys.
- Leak scan and redaction no longer slow down quadratically with many findings (30,000 hits: 42 s to 4 s).
- Leak scan false positives on commit hashes, UUID ids, `data:` URIs, base64 text, `go.sum` and NuGet lockfile
  hashes; binary files are skipped.
- A NUL character in `search_symbols` returns `invalid_argument` instead of an internal error.
- Pre-commit hook pins `language_version: python3.13` and excludes `go.sum` and `packages.lock.json`.

### Upgrading

The index format changed (constant digests, star imports, redaction rules). The next `citegraph index` re-reads
every file; run it once after upgrading.

## 0.1.0

Untagged development version, installed from `main`: Python and C# extractors, cross-service queue links, nine
read-only MCP tools, redaction, leak scan and the evaluation harness.
