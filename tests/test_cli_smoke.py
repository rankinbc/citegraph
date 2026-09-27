from click.testing import CliRunner

from citegraph.cli.main import main


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "citegraph, version 0.1.0" in result.output
