import pytest

from citegraph.models import ToolError
from citegraph.query.common import QueryContext
from citegraph.query.symbols import search_symbols


@pytest.mark.parametrize("query", ["ord\0er", "abc\0", "\0\0\0", "o\0"])
def test_search_symbols_rejects_a_nul_character(sample_ctx: QueryContext, query: str) -> None:
    with pytest.raises(ToolError) as info:
        search_symbols(sample_ctx, query)
    assert info.value.code == "invalid_argument"
    assert "NUL" in info.value.message
