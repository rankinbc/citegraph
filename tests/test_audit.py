from pathlib import Path

from citegraph.audit import AuditLog, summarize
from tests.helpers import load_secret_cases

SECRET = next(c for c in load_secret_cases() if c.kind == "slack-token").value


def test_record_and_read(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.record(
        tool="what_calls", args={"symbol": SECRET}, result_count=2, duration_ms=3.5, client="t", error=None
    )
    log.record(tool="what_calls", args={}, result_count=0, duration_ms=10.0, client="t", error="not_found")
    entries = log.entries()
    assert [e["tool"] for e in entries] == ["what_calls", "what_calls"]
    assert SECRET not in (tmp_path / next(tmp_path.iterdir()).name).read_text(encoding="utf-8")
    stats = summarize(entries)["what_calls"]
    assert stats["count"] == 2
    assert stats["errors"] == 1
    assert stats["p95_ms"] == 10.0
