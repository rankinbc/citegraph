import json
from collections.abc import Callable
from pathlib import Path

import pytest

from citegraph.evaluation.golden import load_golden
from citegraph.evaluation.report import check_regression, write_baseline, write_report
from citegraph.evaluation.runner import Level1Result, run_level1
from citegraph.evaluation.spans import load_corpus_text
from citegraph.indexer import index_root
from citegraph.query.common import QueryContext
from citegraph.store import Store
from tests.fixtures.sample_corpus import BILLING, SHOP
from tests.helpers import FIXTURES

MakeRepo = Callable[[str, dict[str, str]], Path]


@pytest.fixture
def result(make_repo: MakeRepo, repos_root: Path) -> Level1Result:
    make_repo("shop", SHOP)
    make_repo("billing", BILLING)
    stats = index_root(repos_root)
    ctx = QueryContext(Store.open_read_only(Path(stats.db_path)))
    return run_level1(load_golden(FIXTURES / "golden_sample.yaml"), ctx, load_corpus_text(repos_root))


def test_citegraph_scores_perfectly_on_sample(result: Level1Result) -> None:
    assert {q.id: q.citegraph_score.f1 for q in result.questions} == {
        "sample-callers-charge": 1.0,
        "sample-callees-place-order": 1.0,
        "sample-path-main-render": 1.0,
        "sample-config-tax-rate": 1.0,
    }
    assert result.by_tool["find_path"].baseline is None
    assert result.by_tool["what_calls"].baseline is not None
    assert result.p95_ms >= result.p50_ms > 0


def test_calibration_bins_cover_rules_seen(result: Level1Result) -> None:
    rules = {b.rule: b for b in result.calibration}
    assert rules["import_scope"].observed == 1.0
    assert rules["cross_repo_unique"].nominal == 0.6


def test_report_and_regression_gate(result: Level1Result, tmp_path: Path) -> None:
    out = write_report(result, tmp_path / "report", charts=False)
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# citegraph Level-1 eval")
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["questions"]
    assert "| what_calls |" in (out / "readme_table.md").read_text(encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    write_baseline(result, baseline)
    assert check_regression(result, baseline) == []
    baseline.write_text(json.dumps({"what_calls": 1.5}), encoding="utf-8")
    failures = check_regression(result, baseline)
    assert failures and "what_calls" in failures[0]
