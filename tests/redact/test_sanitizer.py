import pytest

from citegraph.redact import configure_extra_patterns, find_secrets, sanitize, sanitize_obj
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
