"""Round 3 (R3-SEC-4, R3-SEC-11): the audit floor's secret-value scan.

Three properties, each with its counter-probe:
  * a key is caught behind a path, an env-style name, a dotted config key, a
    hyphenated header name, an escaped line break and a glued word;
  * slugs, word-like paths, task ids and prose about "bearer" are NOT read as
    secrets (a false hit silently drops the value from the forensic trail);
  * every alternative stays linear on a 100k-character adversarial value.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_FORGE = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))
_cached = sys.modules.get("forge")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    del sys.modules["forge"]

from forge import security_events as se  # noqa: E402

RE = se._AUDIT_SECRET_VALUE_RE

K = "sk-" + "a1B2c3D4e5" * 3                 # legacy OpenAI shape
A = "sk-ant-api03-" + "Ab1_-" * 10           # Anthropic shape
PROJ = "sk-proj-" + "Zx9_q-" * 8


def test_module_under_test_is_the_worktree_copy():
    assert Path(se.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2])


@pytest.mark.parametrize("label,value", [
    ("bare openai", K),
    ("bare anthropic", A),
    ("bare project key", PROJ),
    ("url path", "https://api.example.com/v1/keys/" + K),
    ("env-style name", "OPENAI_API_KEY_" + K),
    ("dotted config key", "cfg.key." + K),
    ("hyphenated header name", "x-api-key-" + A),
    ("escaped newline", "line1\\n" + A),
    ("escaped newline, legacy key", "line1\\n" + K),
    ("letter-glued", "token" + K),
    ("query parameter", "https://h/x?key=" + A),
    ("json", '{"api_key":"' + A + '"}'),
    ("openrouter", "sk-or-v1-" + "0a1b2c3d" * 8),
    ("lower-case hex body", "sk-" + "0a1b2c3d4e5f" * 4),
    ("npm", "npm_" + "aB3" * 12),
    ("aws secret, env form", "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("aws secret, ini form", "AWS_SECRET_ACCESS_KEY = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("aws secret, json form", '"aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'),
    ("bearer header", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"),
    ("bearer, lower case", "bearer abcdefghijklmnop.qrstuvwxyz0123=="),
    ("url userinfo", "postgres://app:s3cr3t@db:5432/x"),
    ("url userinfo, long scheme", "a" * 40 + "://app:s3cr3t@db/x"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("hugging face", "hf_" + "a" * 30),
    ("stripe", "sk_live_" + "a" * 24),
    ("telegram", "123456789:" + "A" * 35),
    ("github", "ghp_" + "a" * 36),
    ("slack", "xoxb-1234567890-1234567890123-" + "a" * 24),
])
def test_secret_shape_is_caught(label, value):
    assert RE.search(value), label
    assert se._audit_value_leaks(value), label


@pytest.mark.parametrize("label,value", [
    ("path with an sk- directory", "/tmp/sk-learn-model-cache-directory-v2/x"),
    ("sk- slug behind an underscore", "my_sk-learn-model-cache-directory-v2"),
    ("slug ending in sk", "task-manager-background-worker-queue"),
    ("risk slug with a year", "risk-assessment-report-2026-final-v2"),
    ("task uuid", "task-0f3a9c2e-4b1d-4e8a-9c3d-112233445566"),
    ("task hex id", "task-0f3a9c2e4b1d4e8a9c3d112233445566"),
    ("xai slug", "xai-model-registry-cache-directory"),
    ("bearer prose", "the bearer authentication_is_not_configured_for_this_route"),
    ("bearer prose, sentence", "a bearer token is required for this endpoint, none was sent"),
    ("url, user only", "ssh://git@github.com/org/repo"),
    ("url with port", "redis://cache:6379/0"),
    ("time and at-sign", "meeting 12:30:45 at user@example"),
    ("dotted identifier", "Module_name_long_identifier12.abcdef.another_long_identifier_part_xyz"),
    ("bare sha1", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
    ("bare 40 base64 chars", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("npm package slug", "npm_config_registry_url_for_internal_mirror"),
])
def test_non_secret_is_not_dropped(label, value):
    assert not RE.search(value), label
    assert not se._audit_value_leaks(value), label


def test_filter_drops_a_path_embedded_key_and_keeps_a_slug_path():
    out, dropped = se.filter_audit_details(
        {"reason": "https://api.example.com/v1/keys/" + K,
         "error": "/tmp/sk-learn-model-cache-directory-v2/x"},
        event_type="tool.created")
    assert "reason" not in out
    assert dropped == ["reason"]
    assert out.get("error") == "/tmp/sk-learn-model-cache-directory-v2/x"


N = 100_000
_ADVERSARIAL = {
    "letters": "a" * N,
    "scheme characters": "a+.-" * (N // 4),
    "userinfo colons": "x://" + "a:" * (N // 2),
    "url without at-sign": "http://" + "u" * N,
    "repeated scheme": "a://" * (N // 4),
    "repeated colon words": "ab:" * (N // 3),
    "repeated eyJ": "eyJ" * (N // 3),
    "eyJ dotted": "eyJ" + ("a" * 9 + ".") * (N // 10),
    "bearer and spaces": "bearer" + " " * N,
    "repeated bearer": "bearer " * (N // 7),
    "repeated sk-": "sk-" * (N // 3),
    "repeated sk-ant-": "sk-ant-" * (N // 7),
    "repeated short slug keys": "sk-a1-" * (N // 6),
    "repeated xai-": "xai-" * (N // 4),
    "digits": "1" * N,
    "digit colon groups": "12345678:" * (N // 9),
    "capital M": "M" * N,
    "discord-like": ("M" + "a" * 23 + ".") * (N // 25),
    "repeated AIza": "AIza" * (N // 4),
    "repeated npm_": "npm_" * (N // 4),
    "aws name repeated": "aws_secret_access_key=" * (N // 22),
    "mixed url": ("ab+cd.ef-gh" * 5 + " ") * (N // 56),
    "pem prefix": "-----BEGIN " + "A " * (N // 2),
}


@pytest.mark.parametrize("label", sorted(_ADVERSARIAL))
def test_scan_is_linear_on_100k_characters(label):
    value = _ADVERSARIAL[label]
    assert len(value) >= N * 0.9
    best = float("inf")
    for _ in range(2):  # best of two: a loaded CI host must not fail the bound
        t0 = time.perf_counter()
        RE.search(value)
        best = min(best, time.perf_counter() - t0)
    assert best < 0.5, f"{label}: {best:.2f}s"
