"""Precision, recall, F1 and percentiles."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel


class Score(BaseModel):
    precision: float
    recall: float
    f1: float


def score(expected: set[str], actual: set[str]) -> Score:
    if not expected and not actual:
        return Score(precision=1.0, recall=1.0, f1=1.0)
    hits = len(expected & actual)
    precision = hits / len(actual) if actual else 0.0
    recall = hits / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return Score(precision=precision, recall=recall, f1=f1)


def mean_score(scores: Sequence[Score]) -> Score:
    if not scores:
        return Score(precision=0.0, recall=0.0, f1=0.0)
    n = len(scores)
    return Score(
        precision=sum(s.precision for s in scores) / n,
        recall=sum(s.recall for s in scores) / n,
        f1=sum(s.f1 for s in scores) / n,
    )


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]
