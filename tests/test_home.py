from pathlib import Path

import pytest

from citegraph.home import index_path


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", ".", "..", "", "with space"])
def test_invalid_index_names_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="invalid index name"):
        index_path(tmp_path, name)


@pytest.mark.parametrize("name", ["eval-corpus", "my_index.v2", "A1"])
def test_valid_index_names(tmp_path: Path, citegraph_home: Path, name: str) -> None:
    path = index_path(tmp_path, name)
    assert path.parent == citegraph_home / "indexes"
    assert path.name.startswith(f"{name}-")
