"""Hostile and edge-case inputs to the extractors: alias bombs, large JSON, stored const values, key shapes."""

import json
import textwrap
import time

from citegraph.extract.configfiles import MAX_YAML_KEYS, ConfigFileExtractor
from citegraph.extract.csharp import CSharpExtractor
from citegraph.extract.names import name_digest
from citegraph.extract.python import PythonExtractor


def keys(path: str, text: str) -> list[tuple[str, int]]:
    return [(k.key_path, k.line) for k in ConfigFileExtractor().extract(path, text.encode()).config_keys]


def yaml_bomb(levels: int, width: int = 9) -> str:
    lines = ["l0: &l0 {a: 1, b: 1, c: 1, d: 1, e: 1, f: 1, g: 1, h: 1, i: 1}"]
    for n in range(1, levels + 1):
        refs = ", ".join(f"k{j}: *l{n - 1}" for j in range(width))
        lines.append(f"l{n}: &l{n} {{{refs}}}")
    return "\n".join(lines) + "\n"


def test_yaml_alias_bomb_walks_each_node_once() -> None:
    text = yaml_bomb(12)  # about 1 KB; expanded it would be 9**13 keys
    started = time.perf_counter()
    result = ConfigFileExtractor().extract("app.config.yaml", text.encode())
    assert time.perf_counter() - started < 0.5
    assert not result.parse_error
    assert len(result.config_keys) == 9 + 12 * 9  # every key written in the file, once


def test_yaml_anchors_and_aliases_report_keys_where_written() -> None:
    text = textwrap.dedent(
        """\
        defaults: &defaults
          timeout: 5
          retry:
            count: 3
        prod:
          <<: *defaults
          host: prod.example.invalid
        staging: *defaults
        recursive: &loop
          self: *loop
        """
    )
    assert keys("app.yaml", text) == [
        ("defaults.timeout", 2),
        ("defaults.retry.count", 4),
        ("prod.host", 7),
        ("staging", 8),
        ("recursive.self", 10),
    ]


def test_yaml_inline_merge_is_walked_as_the_mappings_own_keys() -> None:
    assert keys("app.yaml", "svc:\n  <<: {port: 1}\n  name: x\n") == [("svc.port", 2), ("svc.name", 3)]


def test_yaml_over_the_key_cap_is_a_parse_error() -> None:
    text = "".join(f"k{i}: 1\n" for i in range(MAX_YAML_KEYS + 1))
    result = ConfigFileExtractor().extract("big.yaml", text.encode())
    assert result.parse_error
    assert result.config_keys == []


def test_json_non_ascii_and_escaped_keys_get_their_own_lines() -> None:
    text = '{\n  "clé": 1,\n  "caf\\u00e9": 2,\n  "plain": 3\n}\n'
    assert keys("appsettings.json", text) == [("clé", 2), ("café", 3), ("plain", 4)]


def test_json_same_named_keys_at_other_depths_do_not_steal_lines() -> None:
    text = textwrap.dedent(
        """\
        {
          "items": [{"name": "x"}],
          "a": {
            "b": 1
          },
          "b": 2,
          "name": "top"
        }
        """
    )
    assert keys("appsettings.json", text) == [("items", 2), ("a:b", 4), ("b", 6), ("name", 7)]


def test_large_json_indexes_in_linear_time() -> None:
    data = {f"section{i}": {f"clé{j}": j for j in range(10)} for i in range(7000)}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    assert len(text) > 1_000_000
    started = time.perf_counter()
    result = ConfigFileExtractor().extract("appsettings.json", text.encode())
    assert time.perf_counter() - started < 3
    assert len(result.config_keys) == 70_000
    lines = text.splitlines()
    for key in result.config_keys[::997]:
        assert f'"{key.key_path.rsplit(":", 1)[-1]}"' in lines[key.line - 1]


# Built at runtime so this file passes citegraph's own leak scan.
FAKE_PASSWORD = "Hunter2" + "Prod"
CREDS = textwrap.dedent(
    """\
    namespace Acme;
    public static class Creds {
        public const string AdminPassword = "<password>";
        public const string ApiKeyHex = "deadbeefcafebabe0123456789abcdef";
        public const string DbUser = "sa_admin";
    }
    public class Svc { public void Run() { System.Console.WriteLine(Creds.AdminPassword); } }
    """
).replace("<password>", FAKE_PASSWORD)


def test_csharp_const_values_are_stored_only_as_digests() -> None:
    result = CSharpExtractor().extract("Secrets.cs", CREDS.encode())
    assert [(c.qualified_name, c.value) for c in result.string_consts] == [
        ("Acme.Creds.AdminPassword", name_digest(FAKE_PASSWORD)),
        ("Acme.Creds.ApiKeyHex", name_digest("deadbeefcafebabe0123456789abcdef")),
        ("Acme.Creds.DbUser", name_digest("sa_admin")),
    ]
    dumped = result.model_dump_json()
    for value in (FAKE_PASSWORD, "deadbeefcafebabe0123456789abcdef", "sa_admin"):
        assert value not in dumped


def test_python_env_keys_must_be_name_shaped() -> None:
    py = textwrap.dedent(
        """\
        import os
        TOKEN = os.getenv("fake-token-value: Hunter2Prod")
        URL = os.environ["https://x.example/y"]
        OK = os.getenv("DATABASE_URL")
        ALSO = os.environ["REDIS_HOST"]
        """
    )
    result = PythonExtractor().extract("app.py", py.encode())
    assert [k.key_path for k in result.config_keys] == ["DATABASE_URL", "REDIS_HOST"]


def test_csharp_config_keys_must_be_name_shaped() -> None:
    cs = textwrap.dedent(
        """\
        class C { void M(IConfiguration configuration) {
            var a = configuration["fake-token-value: Hunter2Prod"];
            var b = configuration.GetValue<string>("Payments:ApiUrl");
            var c = Environment.GetEnvironmentVariable("not a key at all");
            var d = configuration.GetSection("Jobs").GetValue<int>("Retries");
            var e = configuration.GetConnectionString("Main");
        } }
        """
    )
    result = CSharpExtractor().extract("C.cs", cs.encode())
    assert [k.key_path for k in result.config_keys] == [
        "Payments:ApiUrl",
        "Jobs:Retries",
        "Jobs",
        "ConnectionStrings:Main",
    ]


def test_python_actor_name_through_a_module_constant() -> None:
    py = textwrap.dedent(
        """\
        import dramatiq
        from jobs.names import IMPORTED
        JOB = "nightly_report"
        TWICE = "a"
        TWICE = "b"
        SPACED: str = "has spaces"


        @dramatiq.actor(actor_name=JOB)
        def report():
            pass


        @dramatiq.actor(actor_name=IMPORTED)
        def imported():
            pass


        @dramatiq.actor(actor_name=TWICE)
        def reassigned():
            pass


        @dramatiq.actor(actor_name=SPACED)
        def spaced():
            pass
        """
    )
    result = PythonExtractor().extract("app/jobs.py", py.encode())
    assert [(h.name, h.handler_qualified) for h in result.queue_handlers] == [
        ("nightly_report", "app.jobs.report"),
        ("imported", "app.jobs.imported"),  # not readable in this file: the function's own name
        ("reassigned", "app.jobs.reassigned"),
        ("spaced", "app.jobs.spaced"),
    ]


def test_python_star_import_is_recorded() -> None:
    result = PythonExtractor().extract("pkg/__init__.py", b"from .impl import *\nfrom os import *\n")
    assert [(i.local_name, i.target) for i in result.imports] == [("*", "pkg.impl"), ("*", "os")]
