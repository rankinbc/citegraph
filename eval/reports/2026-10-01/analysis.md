# Eval analysis 2026-10-01: Python re-export following

Change: Python import lookups follow re-exports (`from .wrappers import Response` in `flask/__init__.py`), up to 3
hops (docs/specs/2026-10-01-cross-service-links-design.md, section 4).

| tool | F1 before (2026-09-28) | F1 after |
|---|---|---|
| what_calls | 0.73 | 0.78 |
| what_does_it_call | 0.84 | 0.84 |
| find_config_key | 0.60 | 0.60 |
| find_path | 0.60 | 0.60 |

Per-question changes: one. `py-what_calls-flask-10` (callers of `flask.Response`): F1 0.00 -> 1.00. Every caller
imports `Response` from the `flask` package, which re-exports it from `flask.wrappers`; the lookup now follows that
import. No question lost precision or recall. Baselines `eval/baseline.json` and `eval/baseline.ci.json` updated.
