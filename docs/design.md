# citegraph - Design Spec

Date: 2026-09-27
Status: Implemented in v0.1.0 (M1)
Companion: agent-guardrails (a separate project)

Sections 1-13 are the design as approved before implementation, with two spots updated to match what shipped
(the `ambiguous` confidence in section 5 and the labeling note in section 9), each pointing to section 14.
Section 14 records the changes made during planning and implementation; where it differs from sections 1-13,
section 14 wins.

## 1. Purpose

citegraph is a read-only MCP server that indexes a folder of git repositories into a local code graph and
answers structural questions for AI coding agents (callers, callees, call paths, config keys, repo overview).
Every answer carries evidence: `path:line` pointers, the commit it was computed at, how it was derived, and a
calibrated confidence score.

It is a public portfolio project aimed at AI/agent tooling engineering roles. The README must convince a
hiring manager in 30 seconds, and a clone-to-first-answer run must take under 5 minutes.

### What makes it different

1. **Evidence-tagged answers.** Source (`parsed` / `derived` / `curated`), `path:line`, commit, staleness and
   confidence on every result, so an agent can cite and judge freshness.
2. **Redaction by construction.** The index stores names and pointers, never source text or config values.
   A sanitizer guards every write and every response; a leak scanner checks the database file itself.
3. **Read-only, audited server.** No write or shell tools; the database is opened read-only; every call is
   logged to JSONL.
4. **Measured, not claimed.** A deterministic eval (vs a grep baseline, with confidence calibration) runs in
   CI; an agent-level eval measures whether agents answer better and cheaper with citegraph than without.

### Constraints

- Clean implementation. No code, names, schemas, hostnames or data from any prior internal work.
- Python 3.13, distributed on PyPI, runnable with `uvx citegraph`.
- MIT license.
- Works on Linux, macOS and Windows.

### Non-goals (v1)

- Embeddings or vector search (candidate v2 experiment, to be scored by the same eval).
- Compiler-precise type resolution (SCIP/LSP). The extractor interface leaves room for a "precise mode" later.
- Web UI, multi-user or hosted server.
- Any write, edit or shell capability exposed to the agent.
- Languages beyond Python, TypeScript/JavaScript and C#.
- Non-git sources.

## 2. Success criteria

| Criterion | Target |
|---|---|
| Clone to first answer on the demo corpus | under 5 minutes on a laptop |
| Full index of ~100k LOC | under 60 s on an 8-core machine |
| Re-index with no changes | under 5 s |
| MCP tool latency | p95 under 200 ms on the demo corpus |
| Level-1 eval | F1 beats the grep baseline on `what_calls` and `what_does_it_call` for every language |
| Calibration | Precision per confidence tier published; each tier within 0.15 of its nominal value, or the tier value is adjusted to match |
| Redaction | Property test green: no fixture secret appears anywhere in the database bytes |
| README | Demo GIF, headline eval chart, 3-command quickstart, security model, limitations |

## 3. Architecture

```
repos root -> ingest -> extract (process pool, per file)
           -> redact -> store -> resolve (graph pass) -> query -> MCP / CLI
```

| Unit | Responsibility | Depends on |
|---|---|---|
| `ingest/` | Discover git repos under a root; list tracked files (`git ls-files`); record HEAD sha; detect language by extension; apply include/exclude globs | git |
| `extract/` | `Extractor` protocol plus `python.py`, `typescript.py`, `csharp.py`, `configfiles.py`. Parse one file, emit `Symbol`, `Reference`, `ConfigKey` records | tree-sitter grammars |
| `redact/` | Sanitizer and leak scanner; also the `leak-scan` CLI and a pre-commit hook | none |
| `store/` | SQLite (WAL). The only write path is `Store.upsert()`, which runs the sanitizer on every string field | redact |
| `resolve/` | Whole-graph pass turning unresolved references into edges, each with a rule and confidence | store |
| `query/` | Pure read functions; each returns `Answer[T]` | store |
| `mcp/` | stdio MCP server (official MCP Python SDK); read-only tools; audit middleware; egress sanitizer | query, redact |
| `cli/` | Click commands | all |
| `eval/` | Corpus fetcher, golden sets, runners, baseline, reports | query |

Boundary rules:

- Extractors never touch the database; they return records.
- The MCP layer never issues SQL; `query/` is the only reader.
- Adding a language means one extractor module and one fixture set, nothing else.

### Extractor protocol

```python
class Extractor(Protocol):
    language: str
    extensions: tuple[str, ...]

    def extract(self, path: RepoPath, source: bytes) -> ExtractResult: ...


class ExtractResult(BaseModel):
    symbols: list[Symbol]
    references: list[Reference]  # unresolved: to_name plus import context
    config_keys: list[ConfigKey]
    imports: list[ImportFact]  # consumed by the resolver
```

Extraction runs in a `ProcessPoolExecutor`, one file per task. A file that fails to parse is recorded in
`index_runs` as an error count and in `citegraph status`; it never aborts the run.

### Incremental indexing

- Each file stores a `content_hash` (BLAKE2b). Unchanged files are skipped.
- Deleted files are removed along with their symbols and references.
- The resolver re-runs over edges touching changed files plus any reference whose `to_name` matches a symbol
  added or removed in this run.

## 4. Data model

SQLite, WAL mode. Only names, locations and metadata are stored.

| Table | Key columns |
|---|---|
| `repos` | `id, name, head_sha, indexed_at` |
| `files` | `id, repo_id, path, lang, content_hash, loc` |
| `symbols` | `id, file_id, kind, name, qualified_name, line_start, line_end, visibility, param_count` |
| `refs` | `id, from_symbol_id, to_name, kind, line, resolved_symbol_id NULL, confidence, rule` |
| `config_keys` | `id, file_id, key_path, key_norm, line, origin` |
| `overrides` | `id, from_qualified, to_qualified, kind, note, source_file, line` |
| `index_runs` | `id, started, finished, repo_shas_json, counts_json, parse_errors, leak_scan_clean` |
| `symbols_fts` | FTS5 trigram index over `name`, `qualified_name` |

- `symbol.kind`: `module`, `class`, `interface`, `function`, `method`.
- `ref.kind`: `call`, `import`, `inherit`, `instantiate`.
- `config_key.origin`: `json`, `yaml`, `env-example`, `code-read`.
- `key_norm` normalizes separators and case so equivalent forms link to one key: `A:B` (.NET configuration),
  `A__B` (environment variable), `a.b` (YAML path).

Signatures and bodies are never stored. An agent that needs code gets a `path:line` pointer and opens the file
with its own Read tool, which keeps the host's permission rules and hooks in play.

### Config key extraction

| Origin | Recognized forms |
|---|---|
| JSON / YAML | Key paths in `appsettings*.json`, `config*.json`, `*.config.yaml`, `docker-compose*.yml` `environment:` blocks (key names only) |
| env example | Keys in `.env.example`, `.env.sample`, `.env.template` |
| Python | `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")` |
| TypeScript | `process.env.X`, `process.env["X"]` |
| C# | `configuration["A:B"]`, `GetSection("A")`, `GetValue<T>("X")`, `Environment.GetEnvironmentVariable("X")` |

Values are never kept. The parser reads a value only to decide whether a node is a leaf; it is discarded
immediately and never passed to the store.

### Curated overrides

An optional `citegraph.overrides.yaml` at the repos root declares edges static parsing cannot see, such as a
service calling another over HTTP:

```yaml
edges:
  - from: ordering.api.OrderService.PlaceOrder
    to: payment.api.PaymentController.Charge
    kind: call
    note: HTTP POST via typed client
```

Overrides are validated against indexed symbols (unknown names are reported by `citegraph status`) and are
returned with `source: curated` and confidence 1.0.

## 5. Evidence envelope

```python
class Evidence(BaseModel):
    repo: str
    path: str
    line: int
    commit: str


class AsOf(BaseModel):
    repo: str
    commit: str
    indexed_at: datetime


class Answer(BaseModel, Generic[T]):
    data: T
    evidence: list[Evidence]
    source: Literal["parsed", "derived", "curated"]
    as_of: list[AsOf]
    confidence: float  # minimum over edges used
    stale: bool  # any touched repo's HEAD moved since indexing
    notes: list[str]
```

`source` is `parsed` for facts read directly from a file, `derived` for anything the resolver inferred, and
`curated` for override edges. A mixed answer reports `derived` if any derived edge was used, otherwise
`curated` if any override was used, otherwise `parsed`.

### Resolver rules and confidence

| Rule | Confidence |
|---|---|
| `direct` - symbol exists as parsed | 1.0 |
| `same_file` | 0.95 |
| `import_scope` - resolved through an import or `using` | 0.9 |
| `same_namespace` - C#: the type is in the referencing namespace or a parent namespace | 0.9 (initial; C# eval pending) |
| `declared_type` - C#: the receiver's declared type, or a base type up to 3 hops, has the member | 0.85 (initial; C# eval pending) |
| `repo_unique` - only one symbol with that name in the repo | 0.7 |
| `cross_repo_unique` - only one symbol with that name across all repos | 0.6 |
| `queue_match` - a job sent by name, matched to its handler (any language) | 0.9 (initial) |
| `curated` - declared in citegraph.overrides.yaml | 1.0 |
| `ambiguous` - n candidates | 0.15, calibrated on the Level-1 eval (was 0.5; `eval/reports/2026-09-28/analysis.md`, section 14 item 10); all candidates listed in `notes` |

Names on a common-name stoplist (`get`, `set`, `run`, `init`, `__init__`, `ToString`, `map`, and similar),
or with more than 10 candidates, are not resolved by `repo_unique`, `cross_repo_unique` or `ambiguous`; they
stay unresolved rather than produce noise.

Language import handling in v1:

- Python: `import a.b`, `from a import b`, relative imports, package roots found by `pyproject.toml` or
  `__init__.py`.
- TypeScript: relative module paths, `index` resolution, workspace package names mapped to repos via
  `package.json`; `tsconfig` path aliases are out of scope for v1.
- C#: see [the C# design spec](specs/2026-10-01-csharp-design.md) and section 14, items 29-34.

### Staleness

At query time the server compares each touched repo's stored `head_sha` with `git rev-parse HEAD`, cached for
60 s per repo. A stale answer is still returned, with `stale: true` and a note suggesting `citegraph index`.

## 6. MCP surface

| Tool | Returns |
|---|---|
| `status()` | Repos, index shas, stale flags, counts, parse errors, override warnings |
| `search_symbols(query, kind?, repo?, limit=25)` | Fuzzy name matches (FTS5 trigram) |
| `get_symbol(name)` | Location, kind, container, children summary; candidates if ambiguous |
| `what_calls(symbol, depth=1, min_confidence=0.5)` | Callers with rule and confidence per edge |
| `what_does_it_call(symbol, depth=1, min_confidence=0.5)` | Callees |
| `find_path(from, to, max_depth=6)` | Shortest call path over edges at or above the threshold |
| `find_config_key(pattern)` | Definitions and code reads of matching keys across repos |
| `repo_overview(repo)` | Languages, top modules, entry points, fan-in hotspots |
| `explain_edge(from, to)` | Rule, evidence, confidence, curated or not |

- Results are capped (default 25, max 200). Truncation adds a note naming the remaining count and the filters
  that would narrow it.
- `depth` is capped at 3.
- Entry points in v1: Python `__main__` blocks and console scripts; `package.json` `main`/`bin`; C#
  `Program.cs` / `static Main`.

### Read-only guarantees (each enforced by a test)

- The server opens SQLite with a `file:...?mode=ro` URI.
- No registered tool writes or runs a subprocess other than the `git rev-parse HEAD` staleness check, which
  uses a fixed argument list. A test fails if the registered tool set differs from the allowlist.
- Every response passes through the sanitizer again before it is returned.

### Audit log

- `~/.citegraph/audit/YYYY-MM-DD.jsonl`, one line per call: `ts, tool, args (sanitized), result_count,
  duration_ms, client, error`.
- Result bodies are never logged.
- `citegraph audit tail` and `citegraph audit stats` (per-tool counts, p50/p95 latency).

### Errors

Tool failures return `{error, message, hint}`, never a traceback.

| Code | Behavior |
|---|---|
| `not_indexed` | Hint: run `citegraph index <root>` |
| `ambiguous_symbol` | Returns candidates with qualified name and path |
| `not_found` | Returns nearest fuzzy matches |
| `invalid_argument` | Names the argument and the accepted range |

## 7. Redaction

Three layers, in order of importance:

1. **Schema.** No table has a column for source text or config values. This is the primary defense.
2. **Sanitizer.** Every string field on write (`Store.upsert`) and on egress (MCP responses, audit args,
   exports) passes `sanitize()`:
   - Known secret shapes: cloud access keys, GitHub/Slack/OpenAI/Anthropic token prefixes, PEM private keys,
     JWTs, connection-string credential segments (keeps `Server`/`Database`, drops `User`/`Password`/`Key`).
   - High-entropy tokens above a length and entropy threshold, except identifiers made of words (decision 40).
   - Matches become `<redacted:kind>`.
   - Extra patterns configurable in `citegraph.toml`.
3. **Leak scanner.** `citegraph leak-scan <paths>` scans files with the same rules; a SQLite file is scanned cell
   by cell (TEXT cells of ordinary tables), so free pages and FTS index blobs are not covered by the scan. Deleted
   rows are zeroed (`secure_delete`), a run that deleted rows optimizes the FTS index, and a rewrite also runs
   VACUUM, so they hold no residue. `index` runs the scan on the database at the end of every run and records
   `leak_scan_clean`. Also shipped as a pre-commit hook.

**String constants.** Job names behind constants are the one stored value: only `const string` values shaped like `^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$`, written through the sanitizing path, so a secret-shaped value is redacted; the leak scan covers the `string_consts` table.

## 8. CLI and configuration

```
citegraph index <root> [--name NAME] [--jobs N]
citegraph serve --root <root> [--name NAME]                         # stdio MCP server
citegraph query <tool> [key=value ...] --root <root> [--name NAME]  # same tools, for humans and scripts
citegraph status --root <root> [--name NAME]
citegraph audit tail [-n N]
citegraph audit stats [--days N]
citegraph leak-scan <paths...> [--fail-on-findings]
citegraph eval fetch [--corpus FILE]
citegraph eval run --golden FILE [--corpus FILE] [--subset all|ci] [--out DIR] [--no-charts]
                   [--check-baseline FILE] [--write-baseline FILE]
citegraph eval report <results.json>
```

`--root` is required wherever it appears; `--name` picks a named index (default: the root folder name).

- Index location: `~/.citegraph/indexes/<name>-<hash8>.db`, where `hash8` is derived from the absolute root
  path. Nothing is written inside the repos.
- Optional `citegraph.toml` at the root: include/exclude globs, languages, overrides path, extra redaction
  patterns.
- Claude Code setup: `claude mcp add citegraph -- uvx citegraph serve --root <root>`.

## 9. Evaluation

### Demo corpus

`eval/corpus.yaml` pins 3 to 5 public repositories at exact commit shas, covering all three languages and at
least one multi-service codebase with shared config. Primary candidate: `dotnet/eShop` (C#). The TypeScript and
Python repos are chosen during planning by size (20k-80k LOC), license (OSI), and presence of cross-module
calls and environment config. `citegraph eval fetch` clones them into a cache directory.

### Level 1: tool-level (deterministic, CI)

- Golden set of about 150 questions (about 50 per language) in `eval/golden/*.yaml`: callers, callees, paths,
  config key definitions and reads.
- Labels: Python and TypeScript sets are bootstrapped once from scip-python and scip-typescript output at the
  pinned shas, then verified against source and committed. The C# set is smaller and labeled directly from
  source. SCIP is a labeling aid only, not a dependency. (How the M1 Python set was actually verified: section
  14, item 14.)
- Metrics: precision, recall and F1 per tool and language; p50/p95 latency; precision per confidence tier
  (calibration).
- Baseline: ripgrep word-boundary heuristic over the same questions.
- CI gate: a fast subset runs on every push; it fails if F1 drops more than 2 points below the committed
  baseline in `eval/baseline.json`.

### Level 2: agent-level (manual, costs API spend)

- About 25 tasks in `eval/agent/tasks.yaml`: `id, prompt, answer_type (set | prose), reference, rubric`.
- Two arms driven by the Claude Agent SDK: (A) Read/Grep/Glob only, (B) the same plus citegraph.
- Models are configurable in `eval/agent/config.toml`; defaults are current Claude models.
- Each task runs 3 times per arm; reports show mean and range.
- Scoring: `set` answers by exact set match (precision/recall); `prose` answers by an LLM judge against the
  rubric and reference answer.
- Metrics: correctness, tool calls, input/output tokens, wall time.
- Output: `eval/reports/<date>/report.md` plus SVG charts (matplotlib). The README embeds the latest charts:
  accuracy and tokens-to-correct-answer, with and without citegraph.
- Negative or mixed results are reported as they are, with analysis.

## 10. Testing

- Extractors: fixture mini-repos per language with snapshot tests of emitted records.
- Resolver: one test per rule showing its tier; stoplist and candidate-cap behavior.
- Incremental: modify, add, delete files and assert the graph matches a full re-index.
- Redaction property test: index a fixture repo seeded with fake secrets in code, config and filenames; scan
  the raw database bytes; fail on any hit.
- Sanitizer: a fake-secret fixture corpus (`tests/fixtures/secrets.toml`: each entry has a kind, a value, and
  whether it must match), including near-misses that must not trigger. agent-guardrails uses the same file
  format so the two pattern sets can be checked against the same cases.
- MCP contract: tool allowlist, read-only connection (write attempt fails), error shapes, truncation notes.
- Tooling: pytest, ruff, pyright strict. CI matrix: Ubuntu and Windows, Python 3.13. Releases publish to PyPI
  via trusted publishing on tags.

## 11. Repository layout

```
citegraph/
  src/citegraph/{ingest,extract,redact,store,resolve,query,mcp,cli}/
  eval/{corpus.yaml,golden/,agent/,reports/,baseline.json}
  tests/{fixtures/,extract/,resolve/,store/,mcp/,redact/}
  docs/{design-notes.md,architecture.md}
  .github/workflows/{ci.yml,release.yml}
  pyproject.toml  README.md  LICENSE  .pre-commit-hooks.yaml
```

No source file over 500 lines.

## 12. README outline

1. One-line pitch.
2. 30-second terminal GIF (vhs): Claude answering a cross-repo question with citations.
3. Headline eval chart.
4. Quickstart: `uvx citegraph index ~/src/demo`, `claude mcp add ...`, ask a question.
5. Design principles: evidence, redaction by construction, read-only, measured.
6. Architecture (mermaid).
7. Eval results and methodology.
8. Security model.
9. Limitations, stated plainly (heuristic resolution, no dynamic dispatch, no tsconfig aliases in v1).
10. Roadmap.

`docs/design-notes.md` is written as a short engineering blog post: problem, decisions, what the eval showed.

## 13. Milestones

- **M1 - first publishable release.** Ingest, Python extractor, config keys (JSON/YAML/env/Python reads),
  redaction, store, resolver, all MCP tools, audit, CLI, Level-1 eval for Python with grep baseline, CI,
  README with GIF and first chart. Tag `v0.1.0`.
- **M2.** TypeScript and C# extractors and config reads, curated overrides, Level-1 eval across all languages
  with calibration chart, Level-2 agent eval and report. Tag `v0.2.0`.

The agent-guardrails companion is built between M1 and M2.

## 14. Amendments from planning and implementation

### From implementation planning (2026-09-27)

1. `refs` holds raw references only. Resolved edges live in a separate `edges` table (`ref_id, from_symbol_id,
   to_symbol_id, kind, rule, confidence, candidates`), because one ambiguous reference resolves to several
   candidates.
2. Additional storage: `imports` (resolver input) and `entry_points` tables; `repos.path`, `files.module`,
   `files.parse_error`, `config_keys.reader_qualified`.
3. M1 re-resolves the whole graph when any file changed and skips resolution when nothing changed. Targeted
   re-resolution (section 3, "Incremental indexing") is deferred unless the timing targets are missed.
4. The grep baseline is a Python regex implementation with ripgrep word-boundary semantics, so CI needs no binary.
5. `find_path` and `explain_edge` take `from_symbol` / `to_symbol`. List-returning graph tools also take `limit`.
6. The `overrides` table ships in M1; the overrides loader ships in M2. `explain_edge` reports `curated: false` in M1.
7. Files over 2 MB are skipped and counted.
8. The leak scanner reads SQLite files cell by cell rather than as raw bytes, because SQLite stores adjacent
   column values back to back and a raw scan reports false high-entropy tokens. A property test still checks the
   raw database bytes for every fixture secret.

### Changes during implementation (2026-09-28)

9. **MCP SDK.** The server targets `mcp>=2.2,<3` and uses the SDK's v2 `MCPServer` API
   (`mcp.server.mcpserver`).
10. **Calibrated `ambiguous` confidence.** The first Level-1 run measured the `ambiguous` rule at 14 of 82
    edges correct (observed precision 0.17) against a nominal 0.5, outside the 0.15 tolerance in section 2, so
    the value was set to 0.15, the nearest 0.05 step. This is in-sample: the value was fitted and checked on the
    same 82 edges of one corpus. `direct` and `cross_repo_unique` produced no edges on that eval and are
    unmeasured. The default `min_confidence` stays 0.5, so ambiguous edges are now hidden by default.
    Before/after F1 is in `eval/reports/2026-09-28/analysis.md`.
11. **Resolver fingerprint.** Stored edges keep the confidence they were resolved with, and item 3 skips
    resolution when no file changed, so a changed confidence table would never reach an existing index. The
    index now stores a fingerprint of the confidence table, the candidate cap and a `RESOLVER_VERSION` constant
    in its `meta` table, and `citegraph index` re-resolves whenever the fingerprint differs, even with no file
    changes. (Amends item 3.)
12. **One read-only open.** Every reader (the MCP server, `citegraph query` and `citegraph status`, and the eval
    runner) opens the index through `Store.open_read_only`, a `file:...?mode=ro` URI connection. It works against
    the WAL database as is, so the planned `PRAGMA query_only` fallback was not needed. A missing index file
    returns a `not_indexed` error with a hint.
13. **Error envelope.** Tool failures return `{error, message, hint, data}`. `data` holds the candidates for
    `ambiguous_symbol` and the nearest matches for `not_found`, and is empty otherwise. An unexpected exception
    returns `internal`; the detail goes to the server log only. (Amends section 6, "Errors".)
14. **M1 corpus and golden set.** The M1 corpus is two Python repositories pinned in `eval/corpus.yaml`:
    pallets/flask 3.1.3 and encode/httpx 0.28.1, 35.6k lines of Python together. The golden set
    (`eval/golden/python.yaml`) has 50 questions: 20 `what_calls`, 20 `what_does_it_call`, 5 `find_config_key`
    and 5 `find_path`. It was bootstrapped from scip-python 0.6.6 output at the pinned shas and verified against
    source by an agent, which opened the source at the referencing line of every expected entry. No human
    reviewed it. (Amends section 9, "Demo corpus" and "Level 1".)
15. **SCIP reader.** Labeling reads a binary `index.scip` with a small built-in protobuf wire reader
    (`citegraph.evaluation.scip_labels.load_scip_index`), so no `scip` CLI is needed on any OS. It prefers
    `typed_range` and falls back to the legacy `range` field, per the SCIP consumer rule. scip-python 0.6.6
    crashed at startup on Windows, so the M1 labels were produced under WSL.
16. **Grep baseline scopes.** The baseline attributes each match to its enclosing `def` or `class` by
    indentation, and opens and closes those scopes only at statement starts (found with Python's `tokenize`),
    so the closing `) -> T:` line of a multi-line signature, or a line inside a docstring, does not end a scope.
    The first committed report tracked scopes by physical line, which attributed function bodies to the
    enclosing class and understated grep. It was fixed before release; `analysis.md` records the correction.
    (Refines item 4.)
17. **Write path and sanitizer.** The single write path is `Store._write` (section 3 calls it
    `Store.upsert()`); it sanitizes every `str` parameter. Connection-string credential values containing `<` or
    `>` are redacted in full, and an existing `<redacted:kind>` marker is never matched again, so sanitizing is
    idempotent and the post-run leak scan does not flag values that are already redacted.

### Final review fixes (2026-09-30)

18. **Entropy rule and paths.** The generic high-entropy token matched across "/", so repo paths with digits
    (`migrations/20240115093000_add_user_table.sql`), absolute repo roots (which made every answer stale) and
    lockfile URLs were redacted. A token holding "/" is now judged whole only when it is base64-shaped: no "-"
    or "_" (the url-safe alphabet replaces "/" with "_"), upper case, lower case and digits present, and at least
    0.45 character-class changes per adjacent pair (random base64 averages about 0.65; words and identifiers far
    less). Any other token holding "/" is judged one segment at a time. A fixture keeps a base64 value containing
    "/" caught as one value. The pre-commit hook excludes lockfiles. (Refines section 7.)
19. **Content fingerprint.** Rows are rewritten only when a file's content hash changes, so a new sanitizer or
    extractor would never reach an existing index. The `meta` table stores a content fingerprint (the citegraph
    version plus a hash of the built-in patterns, the entropy parameters and the extra patterns from
    `citegraph.toml`); when it differs, every file is re-extracted through the sanitizing write path and the graph
    is re-resolved. The resolver fingerprint (item 11) now also covers the stoplist. (Amends section 3,
    "Incremental indexing", and item 11.)
20. **Extra patterns on egress.** `serve`, `query` and `status` load `citegraph.toml` before any tool runs, so
    extra redaction patterns apply to responses and audit lines, not only to the index. The config rejects unknown
    keys and invalid regexes, and every command that reads it reports a bad file as a one-line error.
    (Amends section 7.)
21. **Hidden edges are reported.** Graph answers always report `source: derived`. When `min_confidence` hides
    some of a symbol's own call edges, a note counts them, names their rules and gives the `min_confidence` that
    shows them. An empty answer's confidence is the highest hidden edge's confidence, and 1.0 only when there are
    no call edges at all. (Amends section 5.)
22. **Python package roots deferred to M2.** Section 5 lists "package roots found by `pyproject.toml` or
    `__init__.py`". M1 names a module by its path from the repo root, dropping a top-level `src/` only, so
    imports into packages nested below the root (a monorepo's `services/<name>/<pkg>/`, a `backend/app/` layout)
    resolve only when the import path matches that full path. Changing module naming would rename test modules
    in the pinned eval corpus and invalidate the golden set and the calibration in item 10, so package-root
    discovery moves to M2. The README lists it under Limitations. (Amends section 5.)
23. **No demo GIF in M1.** M1 shipped without the terminal GIF planned in sections 2, 12 and 13. The README shows
    real CLI output on the eval corpus instead; `docs/demo.tape` records the GIF with vhs later.
    (Amends sections 2, 12 and 13.)

### Pre-publish residual fixes (2026-09-30)

24. **Rewrites delete by id, never by stored path.** A stored path was sanitized under the rules in force when it
    was written, and `Store._write` sanitizes every parameter again under the current rules, so a delete keyed by a
    stored path could miss its row: after a new extra pattern matched a stored path, the raw row survived beside
    the redacted one. On a rewrite (item 19) every file row of the repo is deleted by repo id before every file is
    written again; an ordinary deleted file is removed by row id. Stored strings are compared with this run's keys
    but never used as a write parameter. The index opens with SQLite `secure_delete=ON`, so deleted rows are zeroed
    rather than left in free pages. (Amends item 19.)
25. **Assignments and folder prefixes.** `ENTROPY_TOKEN` includes "=", "_" and "-", so a .env line such as
    `AWS_SECRET_ACCESS_KEY=<value>` is one token, and the "_" in the key name made item 18 judge a base64 value
    containing "/" one segment at a time; the same happened after a folder such as `my-app/`. A token without "/"
    is still judged whole. A token holding "/" is first split at each assignment "=" (an "=" followed by a
    character other than "=", so base64 padding never splits), and each part is judged on its own: a part without
    "/", or a base64-shaped one, is judged whole (the 32-character minimum applies to the token, not the part);
    otherwise the remainder after the last segment holding "-" or "_" is judged whole when it is at least 32
    characters, high-entropy and base64-shaped, and every other segment is judged alone. The assignment rule is
    part of the content fingerprint. (Amends item 18.)

### Post-publish cleanup (2026-10-01)

26. **Deleted symbols leave the search index.** `symbols_fts` is an external-content FTS5 table: deleting a symbol
    only records a delete marker, and the deleted row's trigrams stay in older segments until FTS5 merges them, so
    trigrams of a name redacted by a rewrite could survive in live index blobs. After any run that deleted rows (a
    rewrite, or a changed or deleted file, or a repo no longer under the root), `citegraph index` runs the FTS5
    `optimize` command, which merges the index into one segment without them. (Amends item 24.)
27. **VACUUM after a rewrite.** `secure_delete` zeroes only what is deleted while it is on, so an index written by
    an earlier release keeps older deleted content in free pages and in free space inside pages. After a
    content-fingerprint rewrite that deleted rows (the upgrade path, item 19), `citegraph index` commits and runs
    `VACUUM`, which rebuilds the file; the database stays in WAL mode. On the eval corpus index (3.9 MB) it takes
    about 50-70 ms. (Amends item 24.)
28. **A base64 value inside a path.** Item 25 judged a value whole only after the last segment holding "-" or "_",
    so a value followed by such a segment (`my-app/<value>/x_y`, `<value>/my_file.txt`,
    `.../v1/tokens/<value>/revoke_all`) was judged one segment at a time and mostly missed. Path words (segments of
    four or more lower-case letters, such as `backups` or `tokens`) now delimit a value as segments holding "-" or
    "_" do. Each block of consecutive segments between delimiters is judged whole when it is at least 32
    characters, high-entropy and base64-shaped and its longest segment mixes upper- and lower-case letters (a hex
    hash or a lower-case macOS temporary folder never does, even beside a `T` segment); otherwise each segment is
    judged alone, as is each delimiter. On 2000 random values, each of the three shapes is fully redacted 98.2% of
    the time or more (0% before). The path-word pattern is part of the content fingerprint. (Amends item 25.)

### C# support, core (2026-10-01)

Implements [docs/specs/2026-10-01-csharp-design.md](specs/2026-10-01-csharp-design.md) sections 3-5; the C# eval
(section 6) is a separate plan.

29. **C# extraction.** tree-sitter-c-sharp; `.cs` files are indexed by default (`languages = ["python", "csharp"]`).
    References carry the declared type of their receiver when it is a local, parameter, field, property or
    primary-constructor parameter (`refs.receiver_type`, schema version 2). `files.module` holds the file's first
    namespace. Global usings are stored as `*global`, `*global-static` and `*global=<alias>`. Section reads compose
    (`GetSection("A").GetValue("B")` records `A:B`).
30. **Schema 2 upgrade path.** `Store.open` adds `refs.receiver_type` to an older index, and the content fingerprint
    includes the schema version, so the first run after upgrading re-extracts every file.
31. **Language isolation.** Name fallback and qualified lookups only link symbols of the reference's language, so
    Python results are identical with C# repos in the same corpus (verified against the committed Python eval).
32. **C# rules.** In order: `this.`/`base.` members (with base types); declared receiver types (`declared_type`,
    up to 3 base hops; a member the type lacks, such as an extension method, falls through to the name rules);
    enclosing-type members and `using static`;
    type names through aliases, enclosing types, the namespace and its parents (`same_namespace`) and usings; then
    the name rules with the C# stoplist. Every C# inherit reference is resolved before any other reference.
33. **Kind-aware C# lookups.** A C# call only matches methods and `new`/base lists only match types, in name
    fallback and in qualified lookups. On a 32k-line ASP.NET Core solution this cut ambiguous edges from 254 to 48.
    A file outside any namespace names its module symbol after the file stem, so type lookups skip module symbols.
34. **Same-named declarations are one symbol.** C# overloads and partial-type declarations share a qualified name.
    The resolver attaches every C# edge to one canonical declaration per group (the lowest symbol id), and the query
    tools resolve a name to the whole group, so `what_calls`, `what_does_it_call`, `find_path` and `explain_edge`
    work on overloaded methods and partial classes; a module symbol yields to a type of the same name. A local or
    parameter without a known type still shadows an outer declaration of the same name.

### Cross-service links (2026-10-01)

Implements [docs/specs/2026-10-01-cross-service-links-design.md](specs/2026-10-01-cross-service-links-design.md).

35. **Re-export following.** A Python name imported from a package that re-exports it (`from pkg import f` where
    `pkg/__init__.py` imports `f` from a submodule) resolves to the defining symbol; the referencing project's
    modules are searched first. Eval delta: 0.73 -> 0.78.
36. **Projects.** Opt-in with `projects = "auto"` or a list of folders in `citegraph.toml`. Auto detection: a folder
    with `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt` or a `.sln`, or a `.csproj` folder with no
    `.sln` above it; the repository root is never a project. Each project is a logical repo named `<repo>/<folder>`,
    and module names and evidence paths are relative to the folder. If scanning a repository fails (for example a git error), that repository and its projects keep their previous index instead of being dropped.
37. **Queue facts.** Sends are stored as refs of kind `queue` (literal name), `queue_const` (constant) and
    `queue_actor` (Python actor send); handlers go in table `queue_handlers`; name-shaped C# `const string` values go
    in `string_consts`. The schema version is 3, and `[queues] send_methods` is part of the content fingerprint, so
    changing it re-extracts. The stored constant value is the one exception to "no values" (see section 7).
38. **`queue_match` resolution.** A `queue_const` is resolved to its value through the C# type rules, then matched to
    handlers by name in any language. A Python `.send` first resolves its receiver through the Python rules to the
    actor, then links to that handler. Two handlers with one name give `ambiguous` edges.
39. **Curated links.** Entries in `citegraph.overrides.yaml` become refs of kind `curated`, which resolve to edges with rule `curated`, confidence 1.0, and
    the entry's `note` in `refs.note`. Names must match a qualified name exactly, optionally prefixed `repo:`. Entries
    whose names match nothing or more than one are listed by `status` as `overrides_unresolved`. The evidence source
    is the line in the overrides file.

### Identifier redaction (2026-10-01)

40. **Long identifiers are names.** EF Core migration files (`20260615134159_AddCoachConversations.cs`) and long
    test-method names passed the entropy rule: a timestamp or a number next to several words is high-entropy by
    character count. A token of letters, digits, "_" and "-" is exempt when it reads as words: split into number
    runs, words and acronyms, it has at most three number runs, words of mean length 3 or more with vowels and no
    run of five consonants, acronyms of at most five letters and at most one stray letter. On the monorepo that
    showed it, 29 of 30 such names now pass; 0.00%-0.04% of random 32-64 character keys take the exemption, and
    known secret shapes are matched by their own patterns first.
41. **Redacted paths stay distinct.** The indexer keys a file by its sanitized path, so two paths that redacted
    alike shared one row: the second file replaced the first and both were extracted again on every run. A redacted
    path's markers now carry eight hex digits of a hash of the raw path (`<redacted:kind~1a2b3c4d>`).
