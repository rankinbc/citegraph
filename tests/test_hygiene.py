from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_source_file_over_500_lines() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if len(p.read_text(encoding="utf-8").splitlines()) > 500
    ]
    assert offenders == []
