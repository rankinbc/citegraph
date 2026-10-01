"""The query tools over a C# index: callers through interface-typed fields, config keys, overview."""

from __future__ import annotations

from pathlib import Path

import pytest

from citegraph.query.common import QueryContext
from citegraph.query.overview import repo_overview
from tests.fixtures.sample_corpus_csharp import CSHARP_SAMPLE
from tests.helpers import build_store


@pytest.fixture
def cs_ctx(tmp_path: Path) -> QueryContext:
    return QueryContext(build_store(tmp_path / "cs.db", CSHARP_SAMPLE), head_fn=lambda _path: "0" * 40)


def test_repo_overview(cs_ctx: QueryContext) -> None:
    overview = repo_overview(cs_ctx, "ordering").data
    assert set(overview.languages) == {"csharp", "config"}
    assert [(m.module, m.symbols) for m in overview.top_modules] == [
        ("Ordering.Domain", 13),
        ("Ordering.Api", 4),
    ]
    assert [(e.kind, e.name, e.path) for e in overview.entry_points] == [
        ("program-main", "Program", "src/Ordering.Api/Program.cs")
    ]
