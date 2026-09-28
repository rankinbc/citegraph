# Level-1 eval analysis, 2026-09-28

Hand-written companion to the generated `report.md`. Every number here comes from two files:
- `results.json` in this folder: the committed run, with `ambiguous` confidence 0.15.
- `pre-calibration/results.json`: the same run with `ambiguous` at 0.50, kept as the before record.

Both runs use the corrected grep baseline: scope tracking by statement start, commit 8326652.

## Corpus and question set

| repo | pinned release | Python LOC |
|---|---|---|
| pallets/flask | 3.1.3 (22d924701a6ae2e4cd01e9a15bbaf3946094af65) | 17.9k (17,889) |
| encode/httpx | 0.28.1 (26d48e0634e6ee9cdc0533996db289ce4b430177) | 17.8k (17,751) |
| combined | | 35.6k (35,640) |

LOC counts all lines of tracked `.py` files, including tests and examples.

The corpus has 50 questions:

| tool | questions |
|---|---|
| what_calls | 20 |
| what_does_it_call | 20 |
| find_config_key | 5 |
| find_path | 5 |

The golden set (`eval/golden/python.yaml`) was bootstrapped from scip-python 0.6.6 output at the pinned shas and verified against source by an agent. No human reviewed it. Every expected entry was checked by opening the source at the referencing line.

## citegraph vs grep

| tool | questions | citegraph F1 | grep F1 | margin | citegraph P / R | grep P / R |
|---|---|---|---|---|---|---|
| what_calls | 20 | 0.73 | 0.69 | +0.04 | 0.80 / 0.70 | 0.63 / 1.00 |
| what_does_it_call | 20 | 0.84 | 0.58 | +0.26 | 0.88 / 0.84 | 0.50 / 1.00 |
| find_config_key | 5 | 0.60 | 0.86 | -0.26 | 1.00 / 0.46 | 0.78 / 0.98 |
| find_path | 5 | 0.60 | n/a | n/a | 0.60 / 0.60 | n/a |

Unrounded F1:

| tool | citegraph | grep |
|---|---|---|
| what_calls | 0.7283 | 0.6913 |
| what_does_it_call | 0.8367 | 0.5791 |
| find_config_key | 0.5984 | 0.8561 |

- **what_calls:** citegraph beats grep by 0.04, a narrow margin. By question, citegraph scores higher on 6, grep on 5, and 9 tie.
- **what_does_it_call:** citegraph beats grep by 0.26. citegraph is higher on 11 questions, grep on 2, and 7 tie.
- **find_config_key:** citegraph loses to grep by 0.26. Grep is higher on all 5 questions.
- **Where each side wins:** grep's recall is 1.00 on both call tools, because every expected caller and callee has a textual `name(` match. Grep loses only on precision. citegraph's advantage on the call tools is entirely precision; its losses are recall misses. All 7 call questions where grep scores higher are recall misses by citegraph (see Misses).

Correction: the first version of this report showed grep what_calls 0.32 and what_does_it_call 0.48. Those numbers came from a baseline bug. A multi-line signature's closing `) -> T:` line ended the function's scope, so grep attributed body lines to the enclosing class and saw only the parameter list as the body. The bug was fixed in 8326652, and the numbers above supersede the old ones. citegraph's own answers did not change.

## Calibration

Observed precision per resolver rule. It is computed over every edge citegraph returned, at min_confidence 0.0, for the 40 what_calls and what_does_it_call questions.

| rule | nominal | observed | edges |
|---|---|---|---|
| direct | 1.00 | n/a | 0 |
| same_file | 0.95 | 1.00 | 26 |
| import_scope | 0.90 | 1.00 | 17 |
| repo_unique | 0.70 | 0.60 | 5 |
| cross_repo_unique | 0.60 | n/a | 0 |
| ambiguous | 0.15 | 0.17 | 82 |

- **Empty bins:** `direct` and `cross_repo_unique` produced no edges on these questions. The corpus repos do not call each other, so there are no cross-repo edges to judge.
- **repo_unique:** the bin rests on 5 edges. Its 2 wrong edges are one reference pattern in one question: what_does_it_call flask-6, where Jinja's `loader.list_templates()` resolves to the only in-repo `list_templates`, the subject itself.
- **In-sample calibration:** the `ambiguous` value was fitted on these same 82 edges and then checked on them. That shows the value matches this corpus. It is not an out-of-sample validation.
- **Why ambiguous precision is low:** one name-matched reference with n candidates produces n edges, and at most one of them is right. So precision sits near 1/n.

### The calibration change (ambiguous 0.50 -> 0.15)

- The first run measured `ambiguous` at 14 of 82 edges correct: 0.17 against a nominal 0.50, off by 0.33.
- Spec section 2 allows 0.15 of deviation and says the tier value is otherwise adjusted to match. The value was set to 0.15, the nearest 0.05 step.
- The default min_confidence and the eval threshold stay at 0.50, so ambiguous edges are now hidden by default.

| citegraph F1 | ambiguous 0.50 (pre-calibration) | ambiguous 0.15 (committed) | grep F1 (same in both runs) |
|---|---|---|---|
| what_calls | 0.77 (0.7729) | 0.73 (0.7283) | 0.69 (0.6913) |
| what_does_it_call | 0.89 (0.8889) | 0.84 (0.8367) | 0.58 (0.5791) |
| find_path | 0.80 | 0.60 | n/a |
| find_config_key | 0.60 (0.5984) | 0.60 (0.5984) | 0.86 (0.8561) |

- **The trade:** calibration made the confidence values honest and cost recall. Nine questions changed score:
  - Seven lost their only correct edge, or some correct edges, because those edges were ambiguous. Those questions are what_calls flask-3, flask-4, flask-8 and httpx-8; what_does_it_call flask-2, flask-4 and flask-9; and find_path httpx-1.
  - One gained: what_calls flask-7 went from 0.62 to 1.00, because its wrong ambiguous callers disappeared.
- **Margins:** before calibration citegraph beat grep by 0.08 on what_calls and 0.31 on what_does_it_call. After calibration the margins are 0.04 and 0.26.

## Misses (citegraph), by cause

19 of 50 questions score below F1 1.0. The grep fix changed no citegraph answer, so this list is the same as before the fix.

| question | citegraph F1 | missing / extra | cause |
|---|---|---|---|
| what_calls flask-3 | 0.00 | 1 / 0 | receiver-type inference |
| what_calls flask-4 | 0.40 | 3 / 0 | receiver-type inference |
| what_calls flask-6 | 0.00 | 1 / 0 | candidate cap |
| what_calls flask-8 | 0.50 | 4 / 0 | receiver-type inference; imported-object attribute chain |
| what_calls flask-10 | 0.00 | 15 / 0 | re-exports |
| what_calls httpx-1 | 0.67 | 1 / 0 | stoplist |
| what_calls httpx-8 | 0.00 | 1 / 0 | receiver-type inference |
| what_does_it_call flask-2 | 0.00 | 1 / 0 | self in a nested function |
| what_does_it_call flask-4 | 0.40 | 3 / 0 | dunder stoplist; inherited method; receiver-type inference |
| what_does_it_call flask-6 | 0.67 | 0 / 1 | name-only false positive (repo_unique) |
| what_does_it_call flask-9 | 0.00 | 1 / 0 | self in a nested function |
| what_does_it_call httpx-3 | 0.67 | 1 / 0 | stoplist |
| find_config_key (all 5) | 0.15-0.80 | 17 / 0 in total | config extractor scope |
| find_path flask-1 | 0.00 | whole chain | dunder stoplist |
| find_path httpx-1 | 0.00 | whole chain | receiver-type inference |

Causes:

1. **Receiver-type inference (not implemented).**
   - A method called on a local, a parameter or an attribute chain resolves by its name alone. Examples: `bp.add_url_rule`, `s.register`, `app.json.dumps`, `client.patch`, `client.request`.
   - With several same-named definitions the edge is ambiguous (0.15), below the threshold.
   - This is the largest cause of lost recall, and it is what grep's name matching recovers at the cost of precision.
2. **self in a nested function.**
   - The owner-class lookup for `self.m()` applies only when the enclosing symbol is a method. In a closure inside a method, `self.m()` falls back to name matching and becomes ambiguous.
   - Examples: `setupmethod.wrapper_func` and `Scaffold.route.decorator`.
3. **Inherited methods.**
   - `self.m()` where `m` is defined on a base class in another file is not found by the same-file owner lookup, so it too becomes a name match.
   - Example: `self.add_url_rule` in `Flask.__init__`, defined on `App`.
4. **Re-exports.**
   - `flask.Response(...)` and `from flask import Response` do not follow flask's `__init__` re-export to `flask.wrappers.Response`, so all 15 callers are missed.
   - A related case, the imported-object attribute chain: `current_app.json.dumps` has an imported head. The import lookup fails and nothing falls back.
5. **Stoplist and dunders.**
   - `close` and `add` are common names and stay unresolved: what_calls httpx-1 and what_does_it_call httpx-3.
   - `super().__init__(...)` is a dunder and stays unresolved: what_does_it_call flask-4 and find_path flask-1.
6. **Candidate cap.** flask has 12 definitions named `check`, above the cap of 10, so `tag.check(value)` stays unresolved.
7. **Name-only false positive.** A third-party method sharing a name with exactly one in-repo definition resolves as `repo_unique`. Example: Jinja `loader.list_templates()`.
8. **Config extractor scope.** The extractor reads `os.getenv`, `os.environ.get`, `os.environ[...]` and certain config-file formats. It misses:
   - presence checks (`"X" in os.environ`)
   - `monkeypatch.setenv` and `monkeypatch.setitem(os.environ, ...)`
   - test fixtures' tables of variable names
   - Flask app-config keys defined in Python: `default_config` entries, `from_mapping`/`update` keywords, `from_object` attributes, `ConfigAttribute`
   - TOML files other than `pyproject.toml`

   SECRET_KEY finds 1 of 12 locations. Precision is 1.00 on these questions. Separately, WSGI `environ[...]` subscripts are indexed as config reads, but no question covers them.

New after the grep fix:
- The misses above did not change.
- What changed is the comparison: grep is higher on 7 of the 40 call questions. Those are what_calls flask-3, flask-4, flask-6, flask-10 and httpx-8, and what_does_it_call flask-2 and flask-9.
- Every one of them is a citegraph recall miss from causes 1, 2, 4 or 6.

## Latency

- **What is measured:** in-process tool latency, timed around the query function call inside the eval runner. It is not an MCP round trip: there is no JSON-RPC, no stdio transport and no audit-log write.
- **Committed run:** p50 0.55 ms, p95 4.28 ms, max 139.7 ms. `report.md` rounds these to 0.5 and 4.3.
- **The two slow questions:** what_calls flask-1 (120.5 ms) and httpx-1 (139.7 ms). They are the first to touch each repo, which is when the per-repo `git rev-parse HEAD` staleness check runs; it is cached for 60 s afterwards.
- **Run-to-run variation:** the pre-calibration run measured p50 0.60 ms and p95 2.36 ms.

## Labeling judgment calls

These are from the golden file header; each was fixed before citegraph was run.

- **Subject selection:** subjects are library definitions taken in sha256 hash order from the SCIP reference pool.
- **Excluded targets:** targets with more than 15 true callers are not used. httpx `Response` and `Client` were dropped.
- **What counts as a call:** call or instantiation syntax only. These do not count: bare decorators (`@name`), callbacks passed without a call, annotations, isinstance checks, subclassing, and calls through a class-valued attribute such as `self.response_class(...)`.
- **Static dispatch:** `self.m()` resolves to the class the type checker sees, not to subclass overrides. Examples: `Scaffold._check_setup_finished`, `Scaffold.add_url_rule`.
- **Untyped pytest fixtures:** they are typed from their fixture definition, so flask's `app` fixture is a Flask. SCIP misses these calls; 2 pairs were added by reading the source.
- **Nested defs and lambdas:** calls in a nested def belong to the nested def. Calls in a lambda belong to the enclosing named function.
- **Config locations:** reads and writes both count, including test-side setters, resetters and fixture tables. Docstrings, comments and message assertions do not.
- **find_path:** the expected chain is the unique shortest path in the verified graph.
- **CI flag exception:** `py-find_path-flask-2` was flagged `ci: true` after the first eval run. The pre-declared rule had flagged only failing find_path questions, which left the CI gate blind to find_path regressions.

## Known scip_labels gaps

Found on real scip-python 0.6.6 output. The golden set was built from a scratch pool that filtered these cases, so none of them affects it.

- **Import roles:** occurrences on import lines carry `symbol_roles` 8 (ReadAccess) or 1 (Definition), never 2 (Import).
  - flask: 1,226 with role 8 and 78 with role 1.
  - httpx: 652 with role 8 and 46 with role 1.
  - `label_callers`' import skip therefore never fires, and importing modules appear as callers.
- **No package filter:** stdlib and third-party symbols become targets, for example `flask:dumps`.
- **Non-backticked namespaces are lost:** `json/dumps().` parses to a bare `dumps`.
- **Range field:** scip-python 0.6.6 emits only the legacy `range` field, never `typed_range`. The reader handles both.
- **src-layout repos:** scip-python needs `PYTHONPATH=<repo>/src`. Otherwise modules are named `src.flask.*` and test-to-library references stay unresolved.
