import sqlite3
from pathlib import Path

from citegraph.redact.leakscan import Finding, scan_paths
from tests.helpers import FIXTURES, load_secret_cases

SECRET = next(c for c in load_secret_cases() if c.kind == "aws-access-key").value


def test_finding_reports_location_not_value(tmp_path: Path) -> None:
    f = tmp_path / "settings.txt"
    f.write_bytes(f"line one\nkey = {SECRET}\n".encode())
    findings = scan_paths([f])
    assert findings == [Finding(str(f), 2, "aws-access-key")]
    assert SECRET not in str(findings[0])


def test_scans_directories_and_binary(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "blob.db").write_bytes(b"\x00\x01" + SECRET.encode() + b"\xff")
    (tmp_path / "clean.txt").write_text("nothing here", encoding="utf-8")
    findings = scan_paths([tmp_path])
    assert [f.kind for f in findings] == ["aws-access-key"]


def test_clean_file_has_no_findings(tmp_path: Path) -> None:
    f = tmp_path / "ok.txt"
    f.write_text("commit 3f786850e387550fdab836ed7e6dc881de23001b", encoding="utf-8")
    assert scan_paths([f]) == []


def test_sqlite_is_scanned_cell_by_cell(tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(a TEXT, b TEXT)")
    # each half is ordinary; stored back to back they would look like one high-entropy token
    conn.execute("INSERT INTO t VALUES (?, ?)", ("Zx9Qw7Er5Ty3Ui1O", "p0As8Df6Gh4Jk2Lm"))
    conn.execute("INSERT INTO t VALUES (?, ?)", (SECRET, "ok"))
    conn.commit()
    conn.close()
    assert scan_paths([db]) == [Finding(f"{db}#t", 2, "aws-access-key")]


def test_fixture_file_is_leak_scan_clean() -> None:
    assert scan_paths([FIXTURES / "secrets.toml"]) == []
