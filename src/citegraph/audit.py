"""Append-only JSONL audit log: one line per tool call, arguments sanitized, result bodies never logged."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from citegraph.home import audit_dir
from citegraph.redact import sanitize, sanitize_obj


class AuditLog:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or audit_dir()

    def record(
        self,
        *,
        tool: str,
        args: dict[str, object],
        result_count: int,
        duration_ms: float,
        client: str,
        error: str | None,
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        entry = {
            "ts": now.isoformat(),
            "tool": tool,
            "args": sanitize_obj(args),
            "result_count": result_count,
            "duration_ms": round(duration_ms, 2),
            "client": sanitize(client),
            "error": error,
        }
        with (self.directory / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def entries(self, days: int = 1) -> list[dict[str, object]]:
        if not self.directory.is_dir():
            return []
        out: list[dict[str, object]] = []
        for path in sorted(self.directory.glob("*.jsonl"))[-days:]:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(cast(dict[str, object], json.loads(line)))
        return out

    def tail(self, count: int) -> list[dict[str, object]]:
        """The last `count` entries, oldest first, reading day files newest-first until there are enough."""
        if count <= 0 or not self.directory.is_dir():
            return []
        out: list[dict[str, object]] = []
        for path in sorted(self.directory.glob("*.jsonl"), reverse=True):
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            out[:0] = [cast(dict[str, object], json.loads(line)) for line in lines[-(count - len(out)) :]]
            if len(out) >= count:
                break
        return out


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def summarize(entries: list[dict[str, object]]) -> dict[str, dict[str, float]]:
    durations: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    for entry in entries:
        tool = str(entry["tool"])
        durations[tool].append(float(cast(float, entry["duration_ms"])))
        if entry.get("error"):
            errors[tool] += 1
    return {
        tool: {
            "count": len(values),
            "errors": errors[tool],
            "p50_ms": statistics.median(values),
            "p95_ms": _p95(values),
        }
        for tool, values in sorted(durations.items())
    }
