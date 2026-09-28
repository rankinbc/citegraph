import textwrap

from citegraph.extract import EXTRACTORS
from citegraph.extract.configfiles import ConfigFileExtractor
from tests.helpers import load_secret_cases

CONNSTR = next(c for c in load_secret_cases() if c.kind == "connstr-credential").value


def keys(path: str, text: str) -> list[tuple[str, int, str]]:
    result = ConfigFileExtractor().extract(path, text.encode())
    return [(k.key_path, k.line, k.origin) for k in result.config_keys]


def test_registered() -> None:
    assert isinstance(EXTRACTORS["config"], ConfigFileExtractor)


def test_json_key_paths_and_lines_without_values() -> None:
    text = textwrap.dedent(
        f"""\
        {{
          "Logging": {{
            "LogLevel": {{
              "Default": "Information"
            }}
          }},
          "ConnectionStrings": {{
            "Main": "{CONNSTR}"
          }},
          "FeatureFlag": true
        }}
        """
    )
    assert keys("appsettings.json", text) == [
        ("Logging:LogLevel:Default", 4, "json"),
        ("ConnectionStrings:Main", 8, "json"),
        ("FeatureFlag", 10, "json"),
    ]
    dumped = ConfigFileExtractor().extract("appsettings.json", text.encode()).model_dump_json()
    assert CONNSTR not in dumped
    assert "Information" not in dumped


def test_docker_compose_environment_only() -> None:
    text = textwrap.dedent(
        """\
        services:
          api:
            image: shop-api
            environment:
              PAYMENT_API_URL: https://example.invalid
              TAX_RATE: "0.2"
          worker:
            environment:
              - QUEUE_NAME=orders
              - DEBUG
        """
    )
    assert keys("docker-compose.yml", text) == [
        ("PAYMENT_API_URL", 5, "yaml"),
        ("TAX_RATE", 6, "yaml"),
        ("QUEUE_NAME", 9, "yaml"),
        ("DEBUG", 10, "yaml"),
    ]


def test_yaml_leaf_paths() -> None:
    text = "database:\n  host: localhost\n  pool:\n    size: 5\ncache_ttl: 30\n"
    assert keys("app.config.yaml", text) == [
        ("database.host", 2, "yaml"),
        ("database.pool.size", 4, "yaml"),
        ("cache_ttl", 5, "yaml"),
    ]


def test_env_example() -> None:
    text = "# comment\nPAYMENT_API_URL=\nexport TAX_RATE=0.2\n\n"
    assert keys(".env.example", text) == [
        ("PAYMENT_API_URL", 2, "env-example"),
        ("TAX_RATE", 3, "env-example"),
    ]


def test_pyproject_console_scripts() -> None:
    text = '[project]\nname = "shop"\n\n[project.scripts]\nshop = "shop.cli:main"\n'
    result = ConfigFileExtractor().extract("pyproject.toml", text.encode())
    assert [(e.kind, e.name, e.target, e.line) for e in result.entry_points] == [
        ("console-script", "shop", "shop.cli:main", 5)
    ]


def test_invalid_json_sets_parse_error() -> None:
    assert ConfigFileExtractor().extract("appsettings.json", b"{not json").parse_error is True
