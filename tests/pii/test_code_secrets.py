"""core.pii.code_secrets — the credential gate for source/config text (ADR-2241).

Leak cases come from the 2026-10-09 adversarial refutation round of the chat diff;
benign cases are the code shapes that must NOT hide a diff; the timing cases pin that
every detector stays linear (an [ident]*WORD[ident]* regex took 1.4 s on 20 KB).
"""
import time

import pytest

from core.pii.code_secrets import detect_code_secrets
from core.pii.sensitive import PIIDetectionFailedClosed, has_sensitive

LEAKS = [
    " DB_HOST=db\n-DB_PASSWORD=oldS3cret!", "+POSTGRES_PASSWORD=x9k2", "+SECRET_KEY=django-insecure-abc",
    "+GITHUB_TOKEN=abc123def456", "+SLACK_AUTH_TOKEN=xyz12", "+  db_pass: hunter2", "+password: Hunter2!",
    '+  "accessToken": "abcd1234"', "+  password: |", "+spring.datasource.password=hunter2",
    "+export API_TOKEN=abcdef123", "+url = 'postgres://app:Tr0ub4dor&3@db:5432/x'",
    "+redis://:mypassword@localhost:6379/0", '+create_engine("mysql://root:toor@localhost/db")',
    "+machine github.com login me password hunter2", "+_authToken=npm_" + "a" * 36, "+hf_" + "a" * 34,
    "+glpat-" + "a" * 20, "+rk_live_" + "a" * 20, "+SG." + "a" * 20 + "." + "b" * 20,
    "+AccountKey=" + "a" * 40 + "==;", "+SK" + "0" * 32,
    "+x=\u00e9eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
    " MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MaSnDoO8hvQa2rU9",
    '+"api_key": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"', "+-----BEGIN RSA PRIVATE KEY-----",
]
BENIGN = [
    "+    token: str", "+def f(password: str) -> None:", "+    pwd: Optional[str] = None",
    "+access_token = resp.json()['access_token']", "+max_tokens: 100", '+  "token": null',
    "+password = os.environ['DB_PASSWORD']", "+tokenizer = load()", "+if not secret:",
    "+SECRET_KEY = get_secret()", "+password = settings.password", "+token_count = 0",
    "+  token: string;", "+    passed: number;", "+  credential_env: string;",
    "+        token=token_str, kid=token.kid,", "+    # no password ever, and the invoking user",
    "+        credentials: 'same-origin',", " a = 1\n-b = 2\n+b = 20",
]


@pytest.mark.parametrize("text", LEAKS)
def test_leak_shapes_are_caught(text):
    assert detect_code_secrets(text), text


@pytest.mark.parametrize("text", BENIGN)
def test_code_shapes_are_not_secrets(text):
    assert detect_code_secrets(text) == [], text


def test_prose_gate_is_not_widened_by_it():
    # the quoted-key widening was kept OUT of core.pii.sensitive: it hid skill bodies
    assert has_sensitive('send {"username": "...", "token": null} and check the response.') is False


def test_fail_closed_on_non_text():
    with pytest.raises(PIIDetectionFailedClosed):
        detect_code_secrets(b"bytes")  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [
    "pass" * 50000 + "=", ('"token' * 30000) + ":", "x" * 200000 + "=", "a://b:" * 30000,
    "machine password " * 12000, "-" * 200000 + ":", "password" + " " * 200000,
])
def test_linear_time(text):
    t0 = time.perf_counter()
    detect_code_secrets(text)
    assert time.perf_counter() - t0 < 0.5
