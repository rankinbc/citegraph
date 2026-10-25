from click.testing import CliRunner

from citegraph import __version__
from citegraph.cli.main import main


def test_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert f"citegraph, version {__version__}" in result.output
