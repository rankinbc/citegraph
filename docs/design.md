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
| `repo_unique` - only one symbol with that name in the repo | 0.7 |
| `cross_repo_unique` - only one symbol with that name across all repos | 0.6 |
| `ambiguous` - n candidates | 0.15, calibrated on the Level-1 eval (was 0.5; `eval/reports/2026-09-28/analysis.md`, section 14 item 10); all candidates listed in `notes` |

Names on a common-name stoplist (`get`, `set`, `run`, `init`, `__init__`, `ToString`, `map`, and similar),
or with more than 10 candidates, are not resolved by `repo_unique`, `cross_repo_unique` or `ambiguous`; they
stay unresolved rather than produce noise.

Language import handling in v1:

- Python: `import a.b`, `from a import b`, relative imports, package roots found by `pyproject.toml` or
  `__init__.py`.
- TypeScript: relative module paths, `index` resolution, workspace package names mapped to repos via
  `package.json`; `tsconfig` path aliases are out of scope for v1.
- C#: `using` namespaces mapped to declared namespaces.

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
   - High-entropy tokens above a length and entropy threshold.
   - Matches become `<redacted:kind>`.
   - Extra patterns configurable in `citegraph.toml`.
3. **Leak scanner.** `citegraph leak-scan <paths>` scans files, including the raw `.db`, with the same rules.
   `index` runs it on the database at the end of every run and records `leak_scan_clean`. Also shipped as a
   pre-commit hook.

## 8. CLI and configuration

```
citegraph index <root> [--name NAME] [--jobs N]
citegraph serve --root <root>          # stdio MCP server
citegraph query <tool> [args...]       # same tools, for humans and scripts
citegraph status [--root <root>]
citegraph audit tail|stats
citegraph leak-scan <paths...> [--fail-on-findings]
citegraph eval fetch|run|report [--level 1|2]
```

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
