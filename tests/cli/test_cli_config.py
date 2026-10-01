"""citegraph.toml as seen by the CLI: extra redaction patterns on egress and audit, and clean config errors."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

import citegraph.mcp.server as server_mod
from citegraph.cli.main import main
from citegraph.redact import sanitize

MakeRepo = Callable[[str, dict[str, str]], Path]

ACME_CONFIG = 'extra_redaction_patterns = ["acme_internal_[a-z_]+"]\n'


def test_extra_patterns_redact_query_status_and_audit(make_repo: MakeRepo, repos_root: Path) -> None:
    make_repo("acme_internal_tools", {"src/ledger/core.py": "def acme_internal_ledger():\n    pass\n"})
    runner = CliRunner()
    assert runner.invoke(main, ["index", str(repos_root)]).exit_code == 0
    search = ["query", "search_symbols", "query=acme", "--root", str(repos_root)]
    assert "acme_internal_ledger" in runner.invoke(main, search).output  # the index holds the raw name
    (repos_root / "citegraph.toml").write_text(ACME_CONFIG, encoding="utf-8")

    result = runner.invoke(main, search)
    assert result.exit_code == 0, result.output
    assert "acme_internal_" not in result.output
    assert "<redacted:custom>" in result.output

    status = runner.invoke(main, ["status", "--root", str(repos_root)])
    assert status.exit_code == 0, status.output
    assert "acme_internal_" not in status.output

    named = ["query", "search_symbols", "query=acme_internal_ledger", "--root", str(repos_root)]
    assert runner.invoke(main, named).exit_code == 0
    tail = runner.invoke(main, ["audit", "tail", "-n", "1"]).output
    assert json.loads(tail.strip().splitlines()[-1])["args"]["query"] == "<redacted:custom>"
    assert "acme_internal_" not in tail


def test_serve_applies_extra_patterns_before_the_server_runs(
    repos_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repos_root / "citegraph.toml").write_text(ACME_CONFIG, encoding="utf-8")
    seen: list[str] = []

    class FakeServer:
        def run(self) -> None:
            seen.append(sanitize("id acme_internal_ledger"))

    monkeypatch.setattr(server_mod, "build_server", lambda _db_path: FakeServer())
    result = CliRunner().invoke(main, ["serve", "--root", str(repos_root)])
    assert result.exit_code == 0, result.output
    assert seen == ["id <redacted:custom>"]


BAD_CONFIGS = {
    "invalid-toml": ("include = [\n", "invalid TOML"),
    "unknown-key": ('api_token = "x"\n', "api_token: Extra inputs are not permitted"),
    "invalid-regex": ('extra_redaction_patterns = ["[unclosed"]\n', "invalid regex"),
}
COMMANDS = {
    "index": ["index", "{root}"],
    "query": ["query", "status", "--root", "{root}"],
    "status": ["status", "--root", "{root}"],
    "serve": ["serve", "--root", "{root}"],
}


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("bad", BAD_CONFIGS)
def test_bad_config_is_a_clean_error(repos_root: Path, command: str, bad: str) -> None:
    text, expected = BAD_CONFIGS[bad]
    (repos_root / "citegraph.toml").write_text(text, encoding="utf-8")
    args = [a.format(root=repos_root) for a in COMMANDS[command]]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)  # a ClickException, not an uncaught error
    assert "Traceback" not in result.output
    assert "citegraph.toml" in result.output
    assert expected in result.output


def test_eval_run_bad_config_is_a_clean_error(tmp_path: Path, citegraph_home: Path) -> None:
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text("repos: []\n", encoding="utf-8")
    golden = tmp_path / "golden.yaml"
    golden.write_text("questions: []\n", encoding="utf-8")
    root = citegraph_home / "corpus" / "repos"
    root.mkdir(parents=True)
    (root / "citegraph.toml").write_text("include = [\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["eval", "run", "--golden", str(golden), "--corpus", str(corpus)])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    assert "invalid TOML" in result.output
