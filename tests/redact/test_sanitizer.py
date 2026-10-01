import base64
import random
import string

import pytest

from citegraph.redact import configure_extra_patterns, find_secrets, sanitize, sanitize_obj
from citegraph.redact.sanitizer import is_high_entropy, is_identifier_shaped
from tests.helpers import SecretCase, load_secret_cases

CASES = load_secret_cases()
SECRETS = [c for c in CASES if c.must_match]
NEAR_MISSES = [c for c in CASES if not c.must_match]


@pytest.mark.parametrize("case", SECRETS, ids=lambda c: c.kind)
def test_secret_is_redacted(case: SecretCase) -> None:
    out = sanitize(f"value: {case.value} end")
    assert case.value not in out
    assert "<redacted:" in out
    assert out.startswith("value: ")
    assert out.endswith(" end")


@pytest.mark.parametrize("case", NEAR_MISSES, ids=lambda c: c.kind)
def test_near_miss_passes_through(case: SecretCase) -> None:
    assert sanitize(case.value) == case.value
    assert find_secrets(case.value) == []


# Repo paths, repo roots and a lockfile line: digits in a folder or file name must not redact the path.
PATHS = [
    "migrations/20240115093000_add_user_table.sql",
    "app/migrations/0042_auto_20240115_0930.py",
    "alembic/versions/3f9a1c2b7d4e_add_users_table.py",
    "eval/reports/2026-09-28/f1_by_tool.svg",
    "/Users/alice/Documents/GitHub/my-project-2024",
    "/Users/bob/Code/client-work/2023/WebApp-Backend",
    "/home/runner/work/MyProject2024/Backend",
    "/private/var/folders/8b/q1hfx5w9g4k3n2s0000gn/T/pytest-of-alice/pytest-12",
    # uv.lock line 839 (pydantic_core wheel)
    '    { url = "https://files.pythonhosted.org/packages/2c/1e/1d5371213f4cc9a7ed70c0bfcc7911de22311ee99a662a56'
    '077d7292d2ac/pydantic_core-2.46.5-cp313-cp313-win_amd64.whl", hash = "sha256:15f4a94963c95accac15b7b657b'
    'b177d3ad82bb90b0d0526d9a9b85079925db5", size = 2041980, upload-time = "2026-08-28T09:59:12.396Z" },',
]


@pytest.mark.parametrize("text", PATHS)
def test_path_passes_through(text: str) -> None:
    assert sanitize(text) == text
    assert find_secrets(text) == []


def test_base64_with_slash_is_redacted_as_one_value() -> None:
    value = next(c for c in SECRETS if c.kind == "base64-with-slash").value
    assert value.count("/") >= 2
    assert sanitize(f"key {value} end") == "key <redacted:high-entropy> end"


@pytest.mark.parametrize(
    ("kind", "kept"),
    [("env-assignment-base64-slash", "SOME_SERVICE_SECRET_KEY="), ("dir-prefixed-base64-slash", "my-app/")],
)
def test_base64_with_slash_after_a_key_or_folder_is_redacted_as_one_value(kind: str, kept: str) -> None:
    value = next(c for c in SECRETS if c.kind == kind).value
    assert sanitize(f"key {value} end") == f"key {kept}<redacted:high-entropy> end"


def random_base64_with_slash(seed: int, count: int = 2000) -> list[str]:
    rng = random.Random(seed)
    values: list[str] = []
    while len(values) < count:
        value = base64.b64encode(rng.randbytes(30)).decode()  # 40 characters
        if "/" in value:
            values.append(value)
    return values


def fully_redacted(text: str, value: str) -> bool:
    """Every character of `value` lies inside a span `find_secrets` returns; a partly redacted value is a miss."""
    start = text.index(value)
    spans = [(hit.start, hit.end) for hit in find_secrets(text)]
    return all(any(s <= i < e for s, e in spans) for i in range(start, start + len(value)))


def full_redaction_rates(templates: list[str], values: list[str]) -> dict[str, float]:
    return {
        template: sum(fully_redacted(template.format(v), v) for v in values) / len(values)
        for template in templates
    }


def test_random_base64_with_slash_is_fully_redacted_in_env_lines() -> None:
    """Per-character coverage, per embedding, of 2000 values. Measured: seed 20260930 98.55% for each embedding
    but the `DB_<PASSWORD>=` line (100%, caught by the connstr pattern); seed 20261001 98.60% and 100%. The misses
    (29 and 28 per embedding) are values that fail the base64 shape test; 10 of them are partly redacted, which
    a `value not in sanitize(text)` check would count as redacted."""
    templates = [
        "AWS_SECRET_ACCESS_KEY={}",
        "export API_TOKEN={}",
        "- DB_" + PASSWORD_KEY.upper() + "={}",
        "{}",
    ]
    rates = full_redaction_rates(templates, random_base64_with_slash(20260930))
    assert min(rates.values()) >= 0.98, rates


PATH_TEMPLATES = ["my-app/{}/x_y", "{}/my_file.txt", "https://api.example.com/v1/tokens/{}/revoke_all"]


@pytest.mark.parametrize("template", PATH_TEMPLATES)
def test_base64_with_slash_between_path_segments_is_redacted_as_one_value(template: str) -> None:
    value = next(c for c in SECRETS if c.kind == "base64-with-slash").value
    assert (
        sanitize(f"key {template.format(value)} end")
        == f"key {template.format('<redacted:high-entropy>')} end"
    )


def test_random_base64_with_slash_is_fully_redacted_between_path_segments() -> None:
    """Per-character coverage, per embedding, of 2000 values. Measured: seed 20260930 98.25% and seed 20261001
    98.20% for each of the three shapes (before path words delimited a value: 0%, about 32% partly redacted and
    68% missed). The misses are values that fail the base64 shape test on their own."""
    rates = full_redaction_rates(PATH_TEMPLATES, random_base64_with_slash(20260930))
    assert min(rates.values()) >= 0.98, rates


def test_high_entropy_segment_of_a_path_is_redacted_alone() -> None:
    secret = next(c for c in SECRETS if c.kind == "high-entropy").value
    out = sanitize(f"backups/{secret}/dump-2024_01.sql")
    assert out == "backups/<redacted:high-entropy>/dump-2024_01.sql"


def test_connection_string_keeps_server_and_database() -> None:
    conn = next(c for c in SECRETS if c.kind == "connstr-credential").value
    out = sanitize(conn)
    assert "Server=db" in out
    assert "Database=app" in out
    assert "Hunter2Secret!" not in out
    assert "admin" not in out


def test_sanitize_obj_walks_nested_structures() -> None:
    secret = SECRETS[0].value
    out = sanitize_obj({"a": [secret, {"b": secret}], "n": 3, secret: "key"})
    assert secret not in repr(out)
    assert isinstance(out, dict)
    assert out["n"] == 3  # type: ignore[index]


def test_extra_patterns() -> None:
    try:
        configure_extra_patterns([r"ACME-[0-9]{6}"])
        assert sanitize("id ACME-123456 here") == "id <redacted:custom> here"
    finally:
        configure_extra_patterns([])
    assert sanitize("id ACME-123456 here") == "id ACME-123456 here"


# Joined at run time, like the parts in tests/fixtures/secrets.toml, so neither this file nor its compiled
# .pyc contains a literal credential for `citegraph leak-scan` to flag.
PASSWORD_KEY = "".join(["Pass", "word"])


def test_connstr_credential_with_embedded_bracket_is_fully_redacted() -> None:
    out = sanitize(f"{PASSWORD_KEY}=p<ss>word;Database=app")
    assert out == "Password=<redacted:connstr-credential>;Database=app"
    assert "Database=app" in out
    assert out.count("<redacted:connstr-credential>") == 1


def test_connstr_credential_starting_with_bracket_is_fully_redacted() -> None:
    out = sanitize(f"{PASSWORD_KEY}=<3nc0d1ng>Rest;Server=db")
    assert out == "Password=<redacted:connstr-credential>;Server=db"
    assert "Server=db" in out
    assert out.count("<redacted:connstr-credential>") == 1


@pytest.mark.parametrize("case", SECRETS, ids=lambda c: c.kind)
def test_sanitize_is_idempotent(case: SecretCase) -> None:
    once = sanitize(case.value)
    assert sanitize(once) == once
    assert find_secrets(once) == []


# Long identifiers: EF Core migration names, test-method names. A timestamp or a number next to words reads as
# high entropy, but the words make them names, not values.
IDENTIFIERS = [
    "components/bff/src/Spectr.Data/Migrations/20260615134159_AddCoachConversations.Designer.cs",
    "Migrations/20260616052555_AddFeatureFlagsAndJobTier.cs",
    "Spectr.Data.Migrations.20260615232144_AddSubscriptionsAndWebhookEvents",
    "ClassifyStems_WhenVersionMissing_Returns404NotFound",
    "Should_Return_BadRequest_When_StemCountExceeds12",
    "GetUserById_Returns200_WhenUserExists_InDb2",
]


@pytest.mark.parametrize("text", IDENTIFIERS)
def test_long_identifier_passes_through(text: str) -> None:
    assert sanitize(text) == text


def random_high_entropy_tokens(seed: int, alphabet: str, count: int = 5000) -> list[str]:
    rng = random.Random(seed)
    tokens: list[str] = []
    while len(tokens) < count:
        token = "".join(rng.choice(alphabet) for _ in range(rng.choice([32, 40, 48, 64])))
        if is_high_entropy(token) or is_identifier_shaped(token):
            tokens.append(token)
    return tokens


@pytest.mark.parametrize(
    "alphabet",
    [
        string.ascii_letters + string.digits,
        string.ascii_lowercase + string.digits,
        string.ascii_uppercase + string.digits,
        string.ascii_letters + string.digits + "-_",
    ],
    ids=["alnum", "base36", "BASE36", "base64url"],
)
def test_random_tokens_are_rarely_identifier_shaped(alphabet: str) -> None:
    """Of 5000 random high-entropy tokens per alphabet, the share the identifier shape lets through. Measured
    (seed 20261001): 0.00%-0.04%; real identifiers at 32+ characters: 29 of 30 in the monorepo that showed it."""
    tokens = random_high_entropy_tokens(20261001, alphabet)
    assert sum(is_identifier_shaped(t) for t in tokens) / len(tokens) <= 0.001
    assert sum(sanitize(t) == t for t in tokens) / len(tokens) <= 0.001
