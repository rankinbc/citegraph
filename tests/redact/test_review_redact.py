"""Review findings: linear-time scanning, new secret shapes, fewer false positives, the pre-commit hook.

Every fake secret is built at run time from a seeded generator and split prefixes, like the parts in
tests/fixtures/secrets.toml, so neither this file nor its compiled .pyc holds a literal credential for
`citegraph leak-scan` or a push-protection scanner to flag.
"""

import base64
import codecs
import random
import re
import string
import time
from pathlib import Path

import pytest
import yaml

from citegraph.redact import find_secrets, sanitize
from citegraph.redact.leakscan import Finding, scan_paths, scan_text

RNG = random.Random(20261024)
ALNUM = string.ascii_letters + string.digits
URLSAFE = ALNUM + "-_"
HEX = "0123456789abcdef"
REPO = Path(__file__).resolve().parents[2]

PASSWORD = "".join(["Pass", "word"]).lower()
SECRET = "".join(["Sec", "ret"])


def rand(alphabet: str, n: int) -> str:
    """`n` random characters from `alphabet`, with a digit and a letter when the alphabet has them."""
    while True:
        value = "".join(RNG.choice(alphabet) for _ in range(n))
        has_digit = any(c.isdigit() for c in value) or not any(c.isdigit() for c in alphabet)
        has_alpha = any(c.isalpha() for c in value) or not any(c.isalpha() for c in alphabet)
        if has_digit and has_alpha:
            return value


def b64(n: int) -> str:
    return base64.b64encode(RNG.randbytes(n)).decode()


def uuid_like() -> str:
    return "-".join(rand(HEX, n) for n in (8, 4, 4, 4, 12))


def kinds(text: str) -> list[str]:
    return [h.kind for h in find_secrets(text)]


def assert_redacted(text: str, value: str) -> None:
    out = sanitize(text)
    assert value not in out, out
    assert "<redacted:" in out
    assert sanitize(out) == out  # idempotent: a marker is never re-matched
    assert find_secrets(out) == []


# --- M5: linear-time scanning ------------------------------------------------------------------------------------


def many_hits(n: int) -> str:
    return "\n".join(f"h{i} = '{b64(30)}'" for i in range(n))


def test_large_input_with_many_hits_is_linear() -> None:
    """30 000 high-entropy hits (1.5 MB). Measured: 42 s before (quadratic: 2.4 s at 8 000 hits), 4.3 s after."""
    text = many_hits(30_000)
    started = time.perf_counter()
    findings = scan_text(text, "big.py")
    elapsed = time.perf_counter() - started
    assert len(findings) >= 29_000
    assert elapsed < 15, elapsed


def test_line_numbers_are_exact_with_many_hits() -> None:
    lines = [f"v = '{rand(ALNUM, 40)}'" if i % 3 == 0 else "plain text" for i in range(3000)]
    findings = scan_text("\n".join(lines), "f")
    expected = [n for n, line in enumerate(lines, 1) for _ in find_secrets(line)]
    assert len(expected) > 950
    assert [f.line for f in findings] == expected


def test_overlap_keeps_the_earlier_pattern() -> None:
    """Pattern order still decides the kind: a vendor token under a secret-named key keeps its own kind."""
    token = "sk-" + "ant-" + rand(URLSAFE, 30)
    assert kinds(f'{SECRET.lower()}_token: "{token}"') == ["anthropic-key"]


def test_binary_file_is_skipped(tmp_path: Path) -> None:
    value = rand(ALNUM, 40)
    (tmp_path / "image.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00" + value.encode() + bytes(RNG.randbytes(4096))
    )
    assert scan_paths([tmp_path]) == []


def test_nul_after_the_first_chunk_is_still_text(tmp_path: Path) -> None:
    value = rand(ALNUM, 40)
    f = tmp_path / "log.txt"
    f.write_bytes(b"x" * 9000 + b"\n" + value.encode() + b"\n\x00")
    assert scan_paths([f]) == [Finding(str(f), 2, "high-entropy")]


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_utf16_file_with_bom_is_decoded(tmp_path: Path, encoding: str) -> None:
    value = rand(ALNUM, 40)
    bom = codecs.BOM_UTF16_LE if encoding.endswith("le") else codecs.BOM_UTF16_BE
    f = tmp_path / "script.ps1"
    f.write_bytes(bom + f"# header\r\n$k = '{value}'\r\n".encode(encoding))
    assert scan_paths([f]) == [Finding(str(f), 2, "high-entropy")]


# --- Detection misses -------------------------------------------------------------------------------------------


def url_credential_cases() -> list[tuple[str, str]]:
    pw = rand(ALNUM, 12)
    return [
        (f"DATABASE_URL=postgres://app:{pw}@db.internal:5432/prod", pw),
        (f"mongodb+srv://admin:{pw}@cluster0.example.net/db", pw),
        (f"see https://user:{pw}@example.com/ for details", pw),
        (f"redis://:{pw}@cache:6379/0", pw),
    ]


@pytest.mark.parametrize(("text", "value"), url_credential_cases())
def test_url_userinfo_password_is_redacted(text: str, value: str) -> None:
    assert kinds(text) == ["url-credential"]
    assert_redacted(text, value)
    assert "@" in sanitize(text)  # the user and host stay


@pytest.mark.parametrize(
    "text",
    [
        "git@github.com:rankinbc/citegraph.git",
        "ssh://git@github.com:22/org/repo",
        "mailto:someone@example.com",
        "http://localhost:8080/path@v2",
        "postgres://app:${DB_" + PASSWORD.upper() + "}@db:5432/prod",
        "postgres://app:<" + PASSWORD + ">@db:5432/prod",
        "postgres://app:{" + PASSWORD + "}@db/prod",
        "postgres://app:%(" + PASSWORD + ")s@db/prod",
        "https://user:pass@host:port/path",
    ],
)
def test_url_without_a_password_is_not_flagged(text: str) -> None:
    assert find_secrets(text) == []


def keyed_cases() -> list[tuple[str, str]]:
    pw = rand(ALNUM, 14)
    hex40 = rand(HEX, 40)
    django = "django-insecure-" + rand(string.ascii_lowercase + string.digits + "!@#$%^&*(-_=+)", 50)
    return [
        (f"{PASSWORD}: {pw}", pw),  # YAML, unquoted
        (f'{PASSWORD}: "{pw}"', pw),  # YAML, quoted
        (f'{{"{PASSWORD}": "{pw}"}}', pw),  # JSON
        (f'"db{PASSWORD.title()}":"{pw}"', pw),  # JSON camelCase, no spaces
        (f"{SECRET.upper()}_KEY = '{django}'", django),  # Django settings.py
        (f'api_key: "{hex40}"', hex40),  # hex is never high-entropy on its own
        (f"API_TOKEN={hex40}", hex40),
        (f"client_{SECRET.lower()} = '{pw}'", pw),
        (f'auth_token := "{pw}"', pw),  # Go
        (f"'{PASSWORD}' => '{pw}',", pw),  # PHP / Ruby
        (f"connect(host, api_token={pw})", pw),
        (f"heroku_api_key: {uuid_like()}", ""),
    ]


@pytest.mark.parametrize(("text", "value"), keyed_cases())
def test_value_of_a_secret_named_key_is_redacted(text: str, value: str) -> None:
    assert kinds(text) == ["secret-assignment"], text
    if value:
        assert_redacted(text, value)


@pytest.mark.parametrize(
    "text",
    [
        f"def login(user: str, {PASSWORD}: str) -> bool:",
        f"{PASSWORD}: os.environ['DB_{PASSWORD.upper()}']",
        "token: settings.DEPLOY_TOKEN2",
        "token = get_token(request)",
        "private_key: ed25519.Ed25519PrivateKey",
        "MLKEM768X25519PrivateKey = rust_openssl.hpke.MLKEM768X25519PrivateKey",
        "ASC_REQ_NO_TOKEN = 16777216",
        "SEC_I_INCOMPLETE_CREDENTIALS = 0x00090320",
        "szOID_USER_" + PASSWORD.upper() + ' = "2.5.4.35"',
        f"{PASSWORD}: ${{DB_{PASSWORD.upper()}}}",
        f'{PASSWORD}: "<{PASSWORD}>"',
        "token: '{{ secrets.DEPLOY_TOKEN }}'",
        'api_key: "your-api-key-here"',
        f'client_{SECRET.lower()}="my-client-{SECRET.lower()}"',
        f"{PASSWORD}: changeme",
        "if api_token == 'abcdefgh1':",
        "max_tokens: 4096",
        "token_type: bearer-token-v2",
        "PublicKeyToken=b77a5c561934e089",
        f"the {PASSWORD} field is required",
    ],
)
def test_names_expressions_and_placeholders_are_not_values(text: str) -> None:
    assert find_secrets(text) == [], text


def vendor_cases() -> list[tuple[str, str]]:
    return [
        ("gitlab-token", "gl" + "pat-" + rand(URLSAFE, 20)),
        ("shopify-token", "shp" + "at_" + rand(HEX, 32)),
        ("shopify-token", "shp" + "ss_" + rand(HEX, 32)),
        ("shopify-token", "shp" + "ca_" + rand(HEX, 32)),
        ("vault-token", "hv" + "s." + rand(URLSAFE, 24)),
        ("vault-token", "hv" + "s." + rand(URLSAFE, 90)),
        ("discord-token", rand(ALNUM, 24) + "." + rand(URLSAFE, 6) + "." + rand(URLSAFE, 27)),
        ("discord-token", "M" + rand(ALNUM, 25) + "." + rand(URLSAFE, 6) + "." + rand(URLSAFE, 38)),
    ]


@pytest.mark.parametrize(("kind", "value"), vendor_cases())
def test_vendor_token_is_redacted(kind: str, value: str) -> None:
    assert kinds(f"x {value} y") == [kind]
    assert_redacted(f"x {value} y", value)


@pytest.mark.parametrize(
    "text",
    [
        "Spectr.Data.Migrations.20260615232144_AddSubscriptionsAndWebhookEvents",
        "MyCompanyProductNamespace.Common.SomethingSomethingServiceProvider",
        "glpat-short",
        "shpat_" + "z" * 32,
    ],
)
def test_vendor_near_miss_is_not_flagged(text: str) -> None:
    assert find_secrets(text) == []


# --- False positives --------------------------------------------------------------------------------------------


def benign_cases() -> list[str]:
    sha = rand(HEX, 40)
    return [
        f"COMMIT_SHA={sha}",
        f"GIT_HASH={sha}",
        f"commit_sha: {sha}",
        f"BUILD_SHA256={rand(HEX, 64)}",
        f'request_id = "{uuid_like()}"',
        f"REQUEST_ID={uuid_like()}",
        f"traceId={uuid_like()}",
        f"src='data:image/png;base64,{b64(60)}'",
        f"url(data:image/svg+xml;charset=utf-8;base64,{b64(300)})",
        "EXPECTED = '" + base64.b64encode(b"hello world, this is plain test data!").decode() + "'",
        f"github.com/pkg/errors v0.9.1 h1:{b64(32)}",
        f"github.com/pkg/errors v0.9.1/go.mod h1:{b64(32)}",
        f'"contentHash": "{b64(64)}",',
        f'      "sha512": "{b64(64)}",',
        f"  integrity sha512-{b64(64)}",
        f'<script integrity="sha384-{b64(48)}"></script>',
    ]


@pytest.mark.parametrize("text", benign_cases())
def test_hash_id_and_encoded_data_are_not_flagged(text: str) -> None:
    assert find_secrets(text) == [], text


def test_random_value_under_a_hash_key_is_still_judged() -> None:
    """Only a hash-sized value is a digest: a 30-byte random value under `*_HASH` is not one."""
    value = b64(30)
    assert kinds(f"SOME_HASH={value}") == ["high-entropy"]
    assert kinds(f'"contentHash": "{value}"') == ["high-entropy"]


def test_random_value_under_an_id_key_is_still_judged() -> None:
    value = rand(ALNUM, 40)
    assert kinds(f"CLIENT_ID={value}") == ["high-entropy"]


def test_encoded_credentials_are_still_redacted() -> None:
    """Encoded text without spaces (`user:password`) is not exempt; random bytes never are."""
    creds = base64.b64encode(f"admin:{rand(ALNUM, 24)}".encode()).decode()
    assert kinds(f"Authorization: Basic {creds}") == ["high-entropy"]
    assert kinds(f"EXPECTED = '{b64(40)}'") == ["high-entropy"]


def test_data_uri_does_not_hide_a_secret_elsewhere_on_the_line() -> None:
    value = rand(ALNUM, 40)
    assert kinds(f"src='data:image/png;base64,{b64(60)}' key={value}") == ["high-entropy"]


# --- pre-commit hook --------------------------------------------------------------------------------------------


def hook() -> dict[str, object]:
    hooks = yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
    assert isinstance(hooks, list)
    return next(h for h in hooks if h["id"] == "citegraph-leak-scan")  # type: ignore[index]


def test_hook_pins_the_python_the_package_requires() -> None:
    assert hook()["language_version"] == "python3.13"
    assert 'requires-python = ">=3.13"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("path", "excluded"),
    [
        ("go.sum", True),
        ("services/api/go.sum", True),
        ("src/App/packages.lock.json", True),
        ("uv.lock", True),
        ("go.mod", False),
        ("src/App/packages.json", False),
        ("notgo.sum", False),
    ],
)
def test_hook_excludes_lockfiles(path: str, excluded: bool) -> None:
    exclude = hook()["exclude"]
    assert isinstance(exclude, str)
    assert bool(re.search(exclude, path)) is excluded


def test_this_file_is_leak_scan_clean() -> None:
    assert scan_paths([Path(__file__)]) == []
