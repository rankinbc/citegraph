"""Level-1 eval: ask citegraph and the grep baseline the same golden questions and score both."""

from __future__ import annotations

import time
from collections import defaultdict

from pydantic import BaseModel

from citegraph.evaluation.baseline_grep import baseline_answer
from citegraph.evaluation.golden import GoldenQuestion
from citegraph.evaluation.metrics import Score, mean_score, percentile, score
from citegraph.evaluation.spans import CorpusText
from citegraph.keys import normalize_key
from citegraph.models import ToolError
from citegraph.query.common import QueryContext
from citegraph.query.config import find_config_key
from citegraph.query.graph import find_path, what_calls, what_does_it_call
from citegraph.resolve.rules import CONFIDENCE

DEFAULT_THRESHOLD = 0.5


class QuestionResult(BaseModel):
    id: str
    tool: str
    expected: list[str]
    citegraph: list[str]
    citegraph_score: Score
    baseline: list[str] | None
    baseline_score: Score | None
    latency_ms: float


class ToolSummary(BaseModel):
    n: int
    citegraph: Score
    baseline: Score | None


class CalibrationBin(BaseModel):
    rule: str
    nominal: float
    observed: float
    n: int


class Level1Result(BaseModel):
    questions: list[QuestionResult]
    by_tool: dict[str, ToolSummary]
    calibration: list[CalibrationBin]
    p50_ms: float
    p95_ms: float


def _ask(q: GoldenQuestion, ctx: QueryContext, rule_hits: dict[str, list[bool]]) -> list[str]:
    expected = set(q.expected)
    if q.tool in ("what_calls", "what_does_it_call"):
        fn = what_calls if q.tool == "what_calls" else what_does_it_call
        items = fn(ctx, q.args["symbol"], depth=1, min_confidence=0.0, limit=200).data
        for item in items:
            rule_hits[item.rule].append(f"{item.symbol.repo}:{item.symbol.qualified_name}" in expected)
        return sorted(
            {f"{i.symbol.repo}:{i.symbol.qualified_name}" for i in items if i.confidence >= DEFAULT_THRESHOLD}
        )
    if q.tool == "find_path":
        steps = find_path(ctx, q.args["from_symbol"], q.args["to_symbol"]).data
        return [f"{s.symbol.repo}:{s.symbol.qualified_name}" for s in steps]
    key = normalize_key(q.args["pattern"])
    keys = [k for k in find_config_key(ctx, q.args["pattern"], limit=200).data if k.key == key]
    return sorted({f"{loc.repo}:{loc.path}:{loc.line}" for k in keys for loc in (*k.definitions, *k.reads)})


def run_level1(questions: list[GoldenQuestion], ctx: QueryContext, text: CorpusText) -> Level1Result:
    results: list[QuestionResult] = []
    rule_hits: dict[str, list[bool]] = defaultdict(list)
    for q in questions:
        started = time.perf_counter()
        try:
            answer = _ask(q, ctx, rule_hits)
        except ToolError:
            answer = []
        latency = (time.perf_counter() - started) * 1000
        if q.tool == "find_path":
            exact = 1.0 if answer == q.expected else 0.0
            cg_score = Score(precision=exact, recall=exact, f1=exact)
        else:
            cg_score = score(set(q.expected), set(answer))
        base = baseline_answer(q, text)
        results.append(
            QuestionResult(
                id=q.id,
                tool=q.tool,
                expected=q.expected,
                citegraph=answer,
                citegraph_score=cg_score,
                baseline=base,
                baseline_score=None if base is None else score(set(q.expected), set(base)),
                latency_ms=latency,
            )
        )
    by_tool: dict[str, ToolSummary] = {}
    for tool in sorted({r.tool for r in results}):
        rows = [r for r in results if r.tool == tool]
        base_scores = [r.baseline_score for r in rows if r.baseline_score is not None]
        by_tool[tool] = ToolSummary(
            n=len(rows),
            citegraph=mean_score([r.citegraph_score for r in rows]),
            baseline=mean_score(base_scores) if base_scores else None,
        )
    calibration = [
        CalibrationBin(rule=rule, nominal=CONFIDENCE[rule], observed=sum(hits) / len(hits), n=len(hits))
        for rule, hits in sorted(rule_hits.items(), key=lambda kv: -CONFIDENCE[kv[0]])
    ]
    latencies = [r.latency_ms for r in results] or [0.0]
    return Level1Result(
        questions=results,
        by_tool=by_tool,
        calibration=calibration,
        p50_ms=percentile(latencies, 0.5),
        p95_ms=percentile(latencies, 0.95),
    )
