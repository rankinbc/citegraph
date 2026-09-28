# citegraph

A read-only MCP server that turns a folder of git repos into a code graph and answers structural questions for AI
coding agents: who calls this, what does it call, how does A reach B, where is this config key set and read. Every
answer cites its evidence (`repo`, `path:line`, commit), says how it was derived, and carries a calibrated confidence.

<!-- demo.gif: record with `vhs docs/demo.tape` after the PyPI release -->

Who calls `flask.json.loads`? Real output on the eval corpus (flask 3.1.3), trimmed to the first of five callers:

```console
$ uv run citegraph query what_calls symbol=flask:flask.json.loads --root ~/.citegraph/corpus/repos --name eval-corpus
{
  "data": [
    {
      "symbol": {
        "repo": "flask",
        "qualified_name": "flask.json.tag.TaggedJSONSerializer.loads",
        "name": "loads",
        "kind": "method",
        "path": "src/flask/json/tag.py",
        "line_start": 325,
        "line_end": 327,
        "visibility": "public",
        "param_count": 1
      },
      "depth": 1,
      "kind": "call",
      "rule": "import_scope",
      "confidence": 0.9,
      "candidates": 1,
      "path": "src/flask/json/tag.py",
      "line": 327
    },
    ... 4 more callers in tests/test_json.py (lines 75, 102, 130, 185), each import_scope at 0.9
  ],
  "evidence": [
    {
      "repo": "flask",
      "path": "src/flask/json/tag.py",
      "line": 327,
      "commit": "22d924701a6ae2e4cd01e9a15bbaf3946094af65"
    },
    ... one entry per caller
  ],
  "source": "derived",
  "as_of": [
    {
      "repo": "flask",
      "commit": "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
      "indexed_at": "2026-09-28T16:17:03.577574Z"
    }
  ],
  "confidence": 0.9,
  "stale": false,
  "notes": []
}
```

All five callers match the golden set. For the same question the grep baseline returned 21 functions, 5 of them
correct (`py-what_calls-flask-7` in [results.json](eval/reports/2026-09-28/results.json)).

## Results

Level-1 eval on two pinned public repositories, same questions for citegraph and a grep baseline
([methodology](#evaluation), [full report](eval/reports/2026-09-28/report.md),
[analysis](eval/reports/2026-09-28/analysis.md)):

| tool | questions | citegraph F1 | grep F1 | margin | citegraph P / R | grep P / R |
|---|---|---|---|---|---|---|
| `what_calls` | 20 | 0.73 | 0.69 | +0.04 | 0.80 / 0.70 | 0.63 / 1.00 |
| `what_does_it_call` | 20 | 0.84 | 0.58 | +0.26 | 0.88 / 0.84 | 0.50 / 1.00 |
| `find_config_key` | 5 | 0.60 | 0.86 | -0.26 | 1.00 / 0.46 | 0.78 / 0.98 |
| `find_path` | 5 | 0.60 | n/a | n/a | 0.60 / 0.60 | n/a |

- **Callers: a narrow win.** citegraph scores higher on 6 questions, grep on 5, and 9 tie.
- **Callees: a clear win.** citegraph scores higher on 11 questions, grep on 2, and 7 tie.
- **Config keys: grep wins.** citegraph's config extractor misses several ways Python code sets and checks
  keys (see [Limitations](#limitations)).
- Grep's recall is 1.00 on both call tools; citegraph's lead there is precision, and every call question it loses
  is a recall miss.
- Tool latency is p50 0.5 ms and p95 4.3 ms, timed in-process around the query call (not an MCP round trip).
  Indexing the corpus takes about 3 s on a laptop ([measurements](docs/design-notes.md#what-the-eval-showed)).

Confidence per resolver rule against observed precision, over every edge returned for the 40 call questions:

| rule | nominal confidence | observed precision | edges |
|---|---|---|---|
| `same_file` | 0.95 | 1.00 | 26 |
| `import_scope` | 0.90 | 1.00 | 17 |
| `repo_unique` | 0.70 | 0.60 | 5 |
| `ambiguous` | 0.15 | 0.17 | 82 |

`ambiguous` was 0.50 until the first run measured 0.17; it is now 0.15, below the default `min_confidence` of 0.5,
so ambiguous edges are hidden unless a query lowers it. That value was fitted on the same edges it is checked on.

![F1 by tool](eval/reports/2026-09-28/./f1_by_tool.svg) ![Calibration](eval/reports/2026-09-28/./calibration.svg)

## Quickstart

```bash
uvx citegraph index ~/src/my-repos
claude mcp add citegraph -- uvx citegraph serve --root ~/src/my-repos
# then ask Claude Code: "what calls OrderService.place_order?"
```

From a clone of this repository, use `uv run citegraph` in place of `uvx citegraph`. To reproduce the output
above:

```bash
uv run citegraph eval fetch    # clones flask 3.1.3 and httpx 0.28.1 into ~/.citegraph/corpus/repos
uv run citegraph index ~/.citegraph/corpus/repos --name eval-corpus
uv run citegraph query what_calls symbol=flask:flask.json.loads --root ~/.citegraph/corpus/repos --name eval-corpus
```

## Design principles

- **Evidence, not answers.** Results carry `path:line`, commit, `source` (parsed / derived / curated), `confidence`
  and a `stale` flag. The agent opens code with its own tools.
- **Safe by construction.** The index stores names and locations only, never source text or config values. A
  sanitizer runs on every write and every response; the index is leak-scanned after every run.
- **Read-only and audited.** Nine tools, a read-only SQLite connection, no write or shell tools (the only
  subprocess is a fixed `git rev-parse HEAD` staleness check), one JSONL audit line per call
  (`citegraph audit stats`).
- **Measured.** A deterministic eval scores each tool against a grep baseline and checks each confidence tier
  against observed precision. A subset runs in CI as a regression gate. The results above include where
  citegraph loses.

## Tools

| Tool | Answers |
|---|---|
| `status` | what is indexed, is it stale |
| `search_symbols` | fuzzy symbol search |
| `get_symbol` | one symbol, its container and children |
| `what_calls` / `what_does_it_call` | callers / callees, depth 1-3, each edge with rule and confidence |
| `find_path` | shortest call path between two symbols |
| `find_config_key` | where a key is defined and read (`A:B`, `A__B`, `a.b` match) |
| `repo_overview` | languages, top modules, entry points, hotspots |
| `explain_edge` | why an edge exists |

## Architecture

See [docs/architecture.md](docs/architecture.md), the [design notes](docs/design-notes.md) and the full
[design](docs/design.md).

## Evaluation

The Level-1 eval asks 50 questions (20 `what_calls`, 20 `what_does_it_call`, 5 `find_config_key`, 5 `find_path`)
about flask 3.1.3 and httpx 0.28.1, 35.6k lines of Python pinned in [eval/corpus.yaml](eval/corpus.yaml).

- **Labels.** The golden set was bootstrapped from scip-python 0.6.6 output at the pinned commits and verified
  against source by an agent, which opened the source at the referencing line of every expected entry. No human
  reviewed it. The labeling rules are in the header of [eval/golden/python.yaml](eval/golden/python.yaml).
- **Baseline.** A regex search with ripgrep word-boundary semantics that attributes each match to its enclosing
  function by indentation. `find_path` has no grep equivalent.
- **Calibration.** Each resolver rule's nominal confidence against its observed precision, over every edge
  returned for the 40 call questions, whatever its confidence.
- **Reproducible.** `uv run --extra eval citegraph eval run --golden eval/golden/python.yaml` fetches the corpus,
  indexes it and re-runs every question (the `eval` extra adds matplotlib for the charts). CI re-runs a
  19-question subset on every push and fails if any tool's F1 drops more than 0.02 below
  [eval/baseline.ci.json](eval/baseline.ci.json).

The first committed report overstated citegraph's lead because of a scope-tracking bug in the grep baseline. An
audit of the eval caught it before release; [analysis.md](eval/reports/2026-09-28/analysis.md) records the
correction, the calibration change and every miss by cause.

## Security model

citegraph defends against accidental exposure of secrets and source code through an agent's context and logs. It
does not sandbox the agent: pair it with your agent's permission rules. See [the design](docs/design.md), section 7.
The leak scanner is also a pre-commit hook (`citegraph-leak-scan` in `.pre-commit-hooks.yaml`).

## Limitations

- **Python only in v0.1.** TypeScript and C# are next.
- **Heuristic resolution.** Dynamic dispatch, monkey-patching and calls through variables are invisible or
  matched by name, with lower confidence.
- **No receiver-type inference.** A method called on a local, a parameter or an attribute chain
  (`client.request`, `app.json.dumps`) resolves by name alone; with several same-named definitions the edge is
  ambiguous and hidden by default. This is the largest cause of lost recall in the eval.
- **`self` in nested functions and inherited methods.** `self.m()` inside a closure, or where `m` is defined on a
  base class in another file, falls back to name matching.
- **Re-exports are not followed.** `from flask import Response` does not reach `flask.wrappers.Response`, so one
  eval question misses all 15 callers.
- **Stoplist and candidate cap.** Common names (`close`, `add`), dunders such as `super().__init__()`, and names
  with more than 10 definitions are never matched by name alone; unless an import or the same file settles them,
  they stay unresolved rather than produce noise.
- **Config extractor scope.** Keys come from `os.getenv`, `os.environ.get` and `os.environ[...]` in Python, and
  from key names in `appsettings*.json`, `config*.json`, `*.config.yaml`, docker-compose `environment:` blocks
  and `.env` example files. Presence checks (`"X" in os.environ`), `monkeypatch.setenv`, Flask app-config keys
  defined in Python and TOML config files are missed; this is why grep wins `find_config_key`.
- **Calibration is in-sample on one corpus.** `ambiguous` was fitted and checked on the same 82 edges,
  `repo_unique` rests on 5 edges, and `direct` and `cross_repo_unique` produced no edges to measure.

## Roadmap

TypeScript and C# extractors, curated cross-service edges, an agent-level eval (does an agent answer better and
cheaper with citegraph than with grep alone?).

## License

MIT
