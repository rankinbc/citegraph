import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

import citegraph.indexer as indexer_mod
from citegraph.cli.main import main
from citegraph.redact.leakscan import Finding
from tests.fixtures.sample_corpus import BILLING, SHOP
from tests.helpers import load_secret_cases

MakeRepo = Callable[[str, dict[str, str]], Path]


@pytest.fixture
def indexed_root(make_repo: MakeRepo, repos_root: Path) -> Path:
    make_repo("shop", SHOP)
    make_repo("billing", BILLING)
    result = CliRunner().invoke(main, ["index", str(repos_root)])
    assert result.exit_code == 0, result.output
    assert "indexed 2 repos" in result.output
    return repos_root


def test_query_prints_json(indexed_root: Path) -> None:
    result = CliRunner().invoke(main, ["query", "what_calls", "symbol=charge", "--root", str(indexed_root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"][0]["symbol"]["qualified_name"] == "shop.orders.OrderService.place_order"


def test_query_parses_json_values(indexed_root: Path) -> None:
    args = ["query", "what_calls", "symbol=render_invoice", "depth=2", "--root", str(indexed_root)]
    payload = json.loads(CliRunner().invoke(main, args).output)
    assert [i["depth"] for i in payload["data"]] == [1, 2]


def test_query_error_exits_nonzero(indexed_root: Path) -> None:
    result = CliRunner().invoke(
        main, ["query", "get_symbol", "name=nope_nothing", "--root", str(indexed_root)]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "not_found"


def test_unknown_tool(indexed_root: Path) -> None:
    result = CliRunner().invoke(main, ["query", "drop_tables", "--root", str(indexed_root)])
    assert result.exit_code == 2
    assert "unknown tool" in result.output


def test_status_and_audit(indexed_root: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["status", "--root", str(indexed_root)]).exit_code == 0
    stats = runner.invoke(main, ["audit", "stats"])
    assert stats.exit_code == 0
    assert "status" in stats.output
    tail = runner.invoke(main, ["audit", "tail", "-n", "1"])
    assert json.loads(tail.output.strip().splitlines()[-1])["tool"] == "status"


def test_index_without_repos_fails_cleanly(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["index", str(tmp_path)])
    assert result.exit_code == 1
    assert "no git repositories" in result.output
    assert "Traceback" not in result.output


def test_leak_scan(tmp_path: Path) -> None:
    secret = next(c for c in load_secret_cases() if c.kind == "aws-access-key").value
    dirty = tmp_path / "dirty.txt"
    dirty.write_text(f"k={secret}\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("fine\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(main, ["leak-scan", str(clean), "--fail-on-findings"]).exit_code == 0
    result = runner.invoke(main, ["leak-scan", str(dirty), "--fail-on-findings"])
    assert result.exit_code == 1
    assert "dirty.txt:1: aws-access-key" in result.output
    assert secret not in result.output


def test_index_leak_scan_failure_says_what_to_do(
    make_repo: MakeRepo, repos_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_repo("shop", SHOP)
    monkeypatch.setattr(indexer_mod, "scan_paths", lambda _paths: [Finding("index.db#symbols", 1, "custom")])
    result = CliRunner().invoke(main, ["index", str(repos_root)])
    assert result.exit_code == 1
    db_path = result.output.split("index: ", 1)[1].splitlines()[0]
    assert f"delete {db_path}" in result.output
    assert "report a bug" in result.output
