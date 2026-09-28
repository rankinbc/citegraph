import pytest

from citegraph.evaluation.metrics import Score, mean_score, percentile, score


def test_score_basic() -> None:
    s = score({"a", "b"}, {"a", "c"})
    assert (s.precision, s.recall) == (0.5, 0.5)
    assert s.f1 == pytest.approx(0.5)


def test_score_edge_cases() -> None:
    assert score(set(), set()) == Score(precision=1.0, recall=1.0, f1=1.0)
    assert score({"a"}, set()) == Score(precision=0.0, recall=0.0, f1=0.0)
    assert score(set(), {"a"}).precision == 0.0


def test_mean_and_percentile() -> None:
    m = mean_score([Score(precision=1, recall=1, f1=1), Score(precision=0, recall=0, f1=0)])
    assert m.f1 == 0.5
    assert percentile([1.0, 2.0, 3.0, 4.0, 100.0], 0.95) == 100.0
    assert percentile([5.0], 0.5) == 5.0
