from pathlib import Path

import pytest

from citegraph import home as home_mod
from citegraph.home import index_path
from citegraph.keys import normalize_key
from citegraph.models import Answer, ToolError, combine_sources


def test_combine_sources_prefers_weakest() -> None:
    assert combine_sources(["parsed"]) == "parsed"
    assert combine_sources(["parsed", "curated"]) == "curated"
    assert combine_sources(["curated", "derived", "parsed"]) == "derived"
    assert combine_sources([]) == "parsed"


def test_answer_defaults() -> None:
    answer = Answer[list[str]](data=["a"])
    assert answer.source == "parsed"
    assert answer.confidence == 1.0
    assert answer.stale is False
    assert answer.model_dump(mode="json")["data"] == ["a"]


def test_tool_error_shape() -> None:
    err = ToolError(
        "not_found",
        "no symbol 'x'",
        hint="try search_symbols",
        data=[{"qualified_name": "a.x"}],
    )
    assert err.to_dict() == {
        "error": "not_found",
        "message": "no symbol 'x'",
        "hint": "try search_symbols",
        "data": [{"qualified_name": "a.x"}],
    }


def test_home_respects_env(citegraph_home: Path) -> None:
    assert home_mod.citegraph_home() == citegraph_home  # fixture sets CITEGRAPH_HOME


def test_index_path_is_stable_and_root_specific(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert index_path(a) == index_path(a)
    assert index_path(a) != index_path(b)
    assert index_path(a).name.startswith("a-")
    assert index_path(a, name="demo").name.startswith("demo-")
    assert index_path(a).parent == home_mod.citegraph_home() / "indexes"


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Logging:LogLevel", "logging:loglevel"),
        ("LOGGING__LOGLEVEL", "logging:loglevel"),
        ("logging.logLevel", "logging:loglevel"),
        ("AWS_REGION", "aws_region"),
    ],
)
def test_normalize_key(raw: str, norm: str) -> None:
    assert normalize_key(raw) == norm
