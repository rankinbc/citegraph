"""`citegraph eval ...` commands."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import click

from citegraph.config import ConfigError
from citegraph.evaluation.corpus import fetch_corpus, load_corpus
from citegraph.evaluation.golden import load_golden
from citegraph.evaluation.report import check_regression, write_baseline, write_report
from citegraph.evaluation.runner import Level1Result, run_level1
from citegraph.evaluation.spans import load_corpus_text
from citegraph.home import corpus_dir
from citegraph.indexer import index_root
from citegraph.query.common import QueryContext
from citegraph.redact import sanitize
from citegraph.store import Store

FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


@click.group("eval")
def eval_group() -> None:
    """Run the evaluation suite."""


@eval_group.command("fetch")
@click.option("--corpus", "corpus_file", type=FILE, default=Path("eval/corpus.yaml"), show_default=True)
def eval_fetch(corpus_file: Path) -> None:
    """Clone the pinned corpus repos into the citegraph home."""
    for path in fetch_corpus(load_corpus(corpus_file), corpus_dir() / "repos"):
        click.echo(f"ready: {path}")


@eval_group.command("run")
@click.option("--golden", type=FILE, required=True)
@click.option("--corpus", "corpus_file", type=FILE, default=Path("eval/corpus.yaml"), show_default=True)
@click.option("--subset", type=click.Choice(["all", "ci"]), default="all", show_default=True)
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--check-baseline", type=FILE, default=None)
@click.option(
    "--write-baseline", "write_baseline_path", type=click.Path(dir_okay=False, path_type=Path), default=None
)
@click.option("--no-charts", is_flag=True)
def eval_run(
    golden: Path,
    corpus_file: Path,
    subset: str,
    out: Path | None,
    check_baseline: Path | None,
    write_baseline_path: Path | None,
    no_charts: bool,
) -> None:
    """Index the corpus, answer the golden questions, score citegraph against grep."""
    root = corpus_dir() / "repos"
    fetch_corpus(load_corpus(corpus_file), root)
    try:
        stats = index_root(root, name="eval-corpus")
    except ConfigError as exc:
        raise click.ClickException(sanitize(str(exc))) from exc
    click.echo(f"indexed corpus: {stats.symbols} symbols, {stats.edges} edges in {stats.duration_s:.1f}s")
    questions = [q for q in load_golden(golden) if subset == "all" or q.ci]
    ctx = QueryContext(Store.open_read_only(Path(stats.db_path)))
    result = run_level1(questions, ctx, load_corpus_text(root))
    out_dir = write_report(
        result, out or Path("eval/reports") / date.today().isoformat(), charts=not no_charts
    )
    click.echo((out_dir / "readme_table.md").read_text(encoding="utf-8"))
    click.echo(f"report: {out_dir / 'report.md'}")
    if write_baseline_path is not None:
        write_baseline(result, write_baseline_path)
    if check_baseline is not None:
        failures = check_regression(result, check_baseline)
        for failure in failures:
            click.echo(f"REGRESSION {failure}", err=True)
        if failures:
            sys.exit(1)


@eval_group.command("report")
@click.argument("results_json", type=FILE)
def eval_report(results_json: Path) -> None:
    """Re-render report.md and charts from a results.json."""
    result = Level1Result.model_validate(json.loads(results_json.read_text(encoding="utf-8")))
    click.echo(write_report(result, results_json.parent) / "report.md")
