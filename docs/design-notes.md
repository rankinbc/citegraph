# Design notes: citegraph v0.1.0

## The problem

Coding agents rediscover the same codebase every session. Asked who calls a function, an agent greps, reads a
screen of matches, opens files to rule out the wrong ones, and pays in tokens for every step. Its answer sounds as
sure after checking every call site as after guessing from the first three. I wanted an index, built once, that
answers structural questions and says how sure it is.

## Decision: evidence over answers

citegraph never returns code or prose. Every result is a pointer: repo, `path:line`, the commit it was indexed
at, a `source` (parsed, derived or curated), a confidence, and a `stale` flag that turns on when a repo's HEAD has
moved since indexing. The agent opens the file with its own tools, so the host's permission rules and hooks still
apply, and it cites a line instead of paraphrasing a summary.

## Decision: heuristic resolution, measured

Compiler-grade indexers that emit SCIP resolve more precisely, but they need a toolchain per language and a
working environment per repo; scip-python 0.6.6 would not even start on Windows, so the eval labels were made
under WSL. That rules out a one-command install and a first answer within five minutes. citegraph parses with
tree-sitter and resolves each reference with ordered rules, each with a fixed confidence: `same_file` 0.95,
`import_scope` 0.9, `repo_unique` 0.7, `cross_repo_unique` 0.6, and `ambiguous` when several definitions share the
name.

Heuristics are acceptable only if their error rate is known, so the confidences are checked against a golden
set, with SCIP as the offline labeling aid.

## Decision: safe by construction

An index an agent can query is a place for secrets to leak. The main defense is the schema: there is no column
for source text, signatures or config values. Every write goes through one method that sanitizes each string
parameter, and every MCP response and audit line is sanitized again on the way out. Each index run ends with a
leak scan of the database, read cell by cell. The server opens SQLite read-only, exposes nine tools that cannot
write, runs no subprocess except a fixed `git rev-parse HEAD` for staleness, and appends one JSONL audit line per
call. It does not sandbox the agent.

## What the eval showed

The Level-1 eval asks 50 questions about flask 3.1.3 and httpx 0.28.1, 35.6k lines of Python. The golden set was
bootstrapped from scip-python 0.6.6 and verified against source by an agent; no human reviewed it. The baseline
is a word-boundary grep with indentation-based scopes. Numbers are from `eval/reports/2026-09-28/`.

| tool | citegraph F1 | grep F1 | margin |
|---|---|---|---|
| `what_calls` | 0.73 | 0.69 | +0.04 |
| `what_does_it_call` | 0.84 | 0.58 | +0.26 |
| `find_config_key` | 0.60 | 0.86 | -0.26 |
| `find_path` | 0.60 | n/a | n/a |

The callers result is a narrow win: citegraph scores higher on 6 questions, grep on 5, and 9 tie. Grep's recall is
1.00 on both call tools, so citegraph's lead there is precision alone. On config keys grep wins outright: my
extractor reads `os.getenv`-style calls and config files but misses presence checks, `monkeypatch.setenv` and
Flask config keys defined in Python.

The first report showed grep far behind. An audit of the eval before release found why: the baseline let the
closing `) -> T:` line of a multi-line signature end the function's scope, so grep saw only the parameter list as
the body. Scope tracking now uses statement starts. citegraph's answers did not change, its margin shrank, and
the honest margin is what shipped.

Calibration, nominal confidence against observed precision:

| rule | nominal | observed | edges |
|---|---|---|---|
| `same_file` | 0.95 | 1.00 | 26 |
| `import_scope` | 0.90 | 1.00 | 17 |
| `repo_unique` | 0.70 | 0.60 | 5 |
| `ambiguous` | 0.15 | 0.17 | 82 |

`ambiguous` started at 0.50; the first run measured 14 of 82 edges correct, so I set it to 0.15. The index now
stores a resolver fingerprint, so a changed confidence table makes existing indexes re-resolve. The default
threshold stays 0.5, so ambiguous edges are now hidden, and that cost recall: `what_calls` fell from 0.77 to 0.73,
`what_does_it_call` from 0.89 to 0.84 and `find_path` from 0.80 to 0.60, in exchange for confidences that mean
what they say. The fit is in-sample: fitted and checked on the same 82 edges.

19 of the 50 questions score below a perfect F1. The top two causes are missing receiver-type inference, in six
of them (`client.request` resolves by name alone and turns ambiguous), and the config extractor's scope, in all
five config questions.

Performance, measured for this release on a Windows 11 laptop (Intel Core Ultra 7 265H, Python 3.13): full
index 3.1 to 3.2 s, no-change re-index 0.9 to 1.2 s (targets: 60 s for about 100k lines, 5 s). Tool latency in
the report is p50 0.5 ms and p95 4.3 ms, timed in-process rather than over MCP.

## What is next

TypeScript and C# extractors; curated override edges for calls static parsing cannot see, such as HTTP between
services; and the agent-level eval for M2, which runs the same tasks with and without citegraph and compares
correctness, tool calls and tokens. That result decides whether citegraph is worth installing, and I will
publish it the same way, losses included.
