import tomllib
from pathlib import Path

import citegraph

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_source_file_over_500_lines() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if len(p.read_text(encoding="utf-8").splitlines()) > 500
    ]
    assert offenders == []


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == citegraph.__version__
