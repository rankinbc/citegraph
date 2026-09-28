import textwrap

from citegraph.extract.base import module_name_for, package_for
from citegraph.extract.python import PythonExtractor
from citegraph.models import ExtractResult

SRC = textwrap.dedent(
    """\
    import os
    import json as j
    from .models import User, make_user as mk
    from ..core import db


    class Service(Base):
        def __init__(self, repo):
            self.repo = repo

        def save(self, user, *, force=False):
            data = j.dumps(user)
            self.validate(user)
            mk(data)
            return helper(data)

        def validate(self, user):
            pass

        def _audit(self):
            pass


    def helper(x):
        token = os.environ.get("API_TOKEN")
        region = os.environ["AWS_REGION"]
        return Service(x)


    if __name__ == "__main__":
        helper(1)
    """
)
PATH = "src/app/services/core.py"
M = "app.services.core"


def extract(src: str | bytes, path: str = PATH) -> ExtractResult:
    data = src.encode() if isinstance(src, str) else src
    return PythonExtractor().extract(path, data)


def test_module_and_package_names() -> None:
    assert module_name_for("src/flask/app.py") == "flask.app"
    assert module_name_for("src/flask/__init__.py") == "flask"
    assert module_name_for("tests/test_x.py") == "tests.test_x"
    assert module_name_for("setup.py") == "setup"
    assert package_for(PATH) == "app.services"
    assert package_for("src/app/__init__.py") == "app"


def test_symbols() -> None:
    got = {
        s.qualified_name: (s.kind, s.line_start, s.line_end, s.param_count, s.visibility)
        for s in extract(SRC).symbols
    }
    assert got == {
        M: ("module", 1, 31, None, "public"),
        f"{M}.Service": ("class", 7, 21, None, "public"),
        f"{M}.Service.__init__": ("method", 8, 9, 1, "private"),
        f"{M}.Service.save": ("method", 11, 15, 2, "public"),
        f"{M}.Service.validate": ("method", 17, 18, 1, "public"),
        f"{M}.Service._audit": ("method", 20, 21, 0, "private"),
        f"{M}.helper": ("function", 24, 27, 1, "public"),
    }


def test_calls_and_inheritance() -> None:
    refs = {
        (r.from_qualified, r.to_name, r.kind, r.line) for r in extract(SRC).references if r.kind != "import"
    }
    assert refs == {
        (f"{M}.Service", "Base", "inherit", 7),
        (f"{M}.Service.save", "j.dumps", "call", 12),
        (f"{M}.Service.save", "self.validate", "call", 13),
        (f"{M}.Service.save", "mk", "call", 14),
        (f"{M}.Service.save", "helper", "call", 15),
        (f"{M}.helper", "os.environ.get", "call", 25),
        (f"{M}.helper", "Service", "call", 27),
        (M, "helper", "call", 31),
    }


def test_imports_resolve_relative_paths() -> None:
    result = extract(SRC)
    assert {(i.local_name, i.target) for i in result.imports} == {
        ("os", "os"),
        ("j", "json"),
        ("User", "app.services.models.User"),
        ("mk", "app.services.models.make_user"),
        ("db", "app.core.db"),
    }
    import_refs = {r.to_name for r in result.references if r.kind == "import"}
    assert "app.core.db" in import_refs


def test_config_reads_and_entry_point() -> None:
    result = extract(SRC)
    assert [(k.key_path, k.line, k.origin, k.reader_qualified) for k in result.config_keys] == [
        ("API_TOKEN", 25, "code-read", f"{M}.helper"),
        ("AWS_REGION", 26, "code-read", f"{M}.helper"),
    ]
    assert [(e.kind, e.name, e.line) for e in result.entry_points] == [("main-block", M, 30)]


def test_decorators_are_calls_in_enclosing_scope() -> None:
    src = '@app.route("/x")\ndef view():\n    return render("x")\n'
    result = extract(src, "web.py")
    refs = {(r.from_qualified, r.to_name, r.line) for r in result.references}
    assert ("web", "app.route", 1) in refs
    assert ("web.view", "render", 3) in refs
    assert [s.line_start for s in result.symbols if s.name == "view"] == [2]


def test_non_dotted_call_target() -> None:
    result = extract("def f():\n    Foo().bar()\n", "m.py")
    assert {r.to_name for r in result.references} == {"Foo", "?.bar"}


def test_crlf_line_numbers() -> None:
    result = extract(b"def a():\r\n    pass\r\n\r\ndef b():\r\n    a()\r\n", "m.py")
    assert [(s.name, s.line_start) for s in result.symbols if s.kind == "function"] == [("a", 1), ("b", 4)]
    assert [(r.to_name, r.line) for r in result.references] == [("a", 5)]


def test_latin1_source_does_not_crash() -> None:
    result = extract(b"# coding: latin-1\nname = '\xe9'\ndef f():\n    pass\n", "m.py")
    assert [s.name for s in result.symbols if s.kind == "function"] == ["f"]


def test_syntax_error_sets_parse_error() -> None:
    result = extract(b"def broken(:\n    pass\n", "m.py")
    assert result.parse_error is True
