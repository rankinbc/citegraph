import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

import citegraph.cli.main as cli_main
import citegraph.evaluation.corpus as corpus_mod
import citegraph.indexer as indexer_mod
import citegraph.mcp.server as server_mod
from citegraph.cli.main import main
from citegraph.home import index_path
from citegraph.indexer import IndexStats
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


def test_query_string_params_stay_strings(indexed_root: Path) -> None:
    result = CliRunner().invoke(main, ["query", "search_symbols", "query=404", "--root", str(indexed_root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == []


def test_query_rejects_a_non_numeric_depth(indexed_root: Path) -> None:
    args = ["query", "what_calls", "symbol=charge", "depth=two", "--root", str(indexed_root)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 2
    assert "depth must be an integer" in result.output


def test_index_warnings_are_sanitized(repos_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = next(c for c in load_secret_cases() if c.kind == "aws-access-key").value
    stats = IndexStats(db_path="i.db", warnings=[f"git failed: {secret}"])
    monkeypatch.setattr(cli_main, "index_root", lambda *_args, **_kwargs: stats)
    result = CliRunner().invoke(main, ["index", str(repos_root)])
    assert secret not in result.output
    assert "warning: git failed: <redacted:aws-access-key>" in result.output


def test_index_jobs_must_be_positive(repos_root: Path) -> None:
    result = CliRunner().invoke(main, ["index", str(repos_root), "--jobs", "0"])
    assert result.exit_code == 2
    assert "--jobs" in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["index", "{root}"],
        ["status", "--root", "{root}"],
        ["query", "status", "--root", "{root}"],
        ["serve", "--root", "{root}"],
    ],
)
def test_bad_index_name_is_a_clean_error(repos_root: Path, command: list[str]) -> None:
    args = [a.format(root=repos_root) for a in command] + ["--name", "../escape"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 2, result.output
    assert "invalid index name" in result.output
    assert "Traceback" not in result.output


def test_eval_fetch_without_git_is_a_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(
        "repos:\n  - {name: r, url: https://example.invalid/r.git, sha: '" + "0" * 40 + "', lang: python}\n",
        encoding="utf-8",
    )

    def no_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(corpus_mod.subprocess, "run", no_git)
    result = CliRunner().invoke(main, ["eval", "fetch", "--corpus", str(corpus)])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "could not run git" in result.output


def _audit_line(tool: str) -> str:
    entry = {
        "ts": "t",
        "tool": tool,
        "args": {},
        "result_count": 0,
        "duration_ms": 1.0,
        "client": "t",
        "error": None,
    }
    return json.dumps(entry) + "\n"


def test_audit_tail_reads_back_across_day_files(citegraph_home: Path) -> None:
    audit_dir = citegraph_home / "audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "2026-09-28.jsonl").write_text("".join(_audit_line(t) for t in "abc"), encoding="utf-8")
    (audit_dir / "2026-09-29.jsonl").write_text(_audit_line("d"), encoding="utf-8")
    result = CliRunner().invoke(main, ["audit", "tail", "-n", "3"])
    assert result.exit_code == 0, result.output
    assert [json.loads(line)["tool"] for line in result.output.splitlines()] == ["b", "c", "d"]


def test_tilde_roots_are_expanded(
    make_repo: MakeRepo, repos_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_repo("shop", SHOP)
    monkeypatch.setenv("HOME", str(repos_root.parent))  # POSIX
    monkeypatch.setenv("USERPROFILE", str(repos_root.parent))  # Windows
    tilde = "~/" + repos_root.name
    runner = CliRunner()
    assert runner.invoke(main, ["index", tilde]).exit_code == 0
    for args in (["status", "--root", tilde], ["query", "status", "--root", tilde]):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output
    served: list[Path] = []

    class FakeServer:
        def run(self) -> None:
            pass

    def fake_build_server(db_path: Path) -> FakeServer:
        served.append(db_path)
        return FakeServer()

    monkeypatch.setattr(server_mod, "build_server", fake_build_server)
    assert runner.invoke(main, ["serve", "--root", tilde]).exit_code == 0
    assert served == [index_path(repos_root)]
