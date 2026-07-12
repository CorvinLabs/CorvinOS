"""Regression tests for ACS release-blocking hardening fixes.

Covers three confirmed findings:

  H5 — acs_gate_chain._run_critique_sync referenced an unbound ``log`` name,
        raising NameError (not caught by the surrounding except) when the
        gate-3 critique subprocess exited non-zero (auth error / rate limit),
        failing the whole ACS run instead of gate-3's pass-by-default.

  H2 — acs_runtime._call_worker_sync copied os.environ into the worker
        subprocess env WITHOUT stripping the operator's real Anthropic
        credentials, so a prompt-injected worker (full tools, --max-turns 20)
        could exfiltrate the live key via Bash.

  M9 — acs_runtime labelled claude_code worker locality as "eu_cloud" in the
        GDPR Art. 30 audit trail, but api.anthropic.com is US jurisdiction
        (us_cloud). Only genuine local/hermes must stay "local".
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

try:
    from . import acs_gate_chain as _gc
    from . import acs_runtime as _rt
except ImportError:  # pragma: no cover - direct-run fallback
    import acs_gate_chain as _gc  # type: ignore[no-redef]
    import acs_runtime as _rt  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# H5 — gate-3 critique non-zero exit must not raise NameError
# ---------------------------------------------------------------------------

def test_h5_critique_nonzero_exit_returns_none_not_nameerror():
    """A non-zero claude-p exit hits the ``log.debug`` branch. Before the fix
    that raised NameError (log unbound); now it must log-and-return None."""
    fake_proc = MagicMock(returncode=1, stdout="", stderr="auth error")
    with patch.object(_gc.shutil, "which", return_value="/usr/bin/claude"), \
            patch.object(_gc.subprocess, "run", return_value=fake_proc):
        # Must NOT raise NameError; gate-3 falls through to pass-by-default None.
        result = _gc._run_critique_sync("some output", "some goal", "claude-haiku-4-5")
    assert result is None


def test_h5_module_binds_logger():
    """The module must expose a real logging.Logger named ``log``."""
    import logging
    assert isinstance(getattr(_gc, "log", None), logging.Logger)


# ---------------------------------------------------------------------------
# H2 + M9 — shared worker-subprocess harness
# ---------------------------------------------------------------------------

def _run_worker_capture(monkeypatch, engine_id="claude_code"):
    """Invoke _call_worker_sync with all external effects mocked, capturing
    the env dict handed to subprocess.Popen and the returned attestation."""
    captured = {}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            self._pid = 4242

        def communicate(self, input=None, timeout=None):
            return ("", "")

        def poll(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(_rt, "_resolve_worker_engine",
                        lambda model, tenant_id=None: (engine_id, model))
    monkeypatch.setattr(_rt, "_assert_engine_licensed", lambda *a, **k: None)
    monkeypatch.setattr(_rt, "_claude_binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(_rt.shutil, "which", lambda *a, **k: "/usr/bin/claude")
    # Provider redirect stays a no-op so the test doesn't need live config;
    # its real job (inject proxy creds) is orthogonal to the cred-strip check.
    monkeypatch.setattr(_rt, "_apply_provider_redirect", lambda env, tid: None)
    monkeypatch.setattr(_rt.subprocess, "Popen", _FakePopen)

    output, tokens, attestation = _rt._call_worker_sync(
        "do a task", "system prompt", "claude-sonnet-4-6",
        {"timeout_seconds": 30, "max_worker_turns": 20},
    )
    return captured, attestation


def test_h2_worker_env_strips_real_anthropic_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-SECRET")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "real-auth-token")

    captured, _ = _run_worker_capture(monkeypatch)

    env = captured["env"]
    assert env is not None
    assert "ANTHROPIC_API_KEY" not in env, "live API key must not reach worker subprocess"
    assert "ANTHROPIC_AUTH_TOKEN" not in env, "live auth token must not reach worker subprocess"
    # Real process env is untouched (only the copy is stripped).
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-REAL-SECRET"


def test_h2_worker_env_strips_all_provider_and_pii_secrets(monkeypatch):
    """round-2: the 2-var ANTHROPIC-only strip missed OPENAI / provider /
    messaging / license creds that operators demonstrably have in env."""
    secrets = {
        "OPENAI_API_KEY": "sk-openai-REAL",
        "OPENAI_APIKEY": "sk-openai2-REAL",
        "CORVIN_STT_OPENAI_KEY": "sk-stt-REAL",
        "CORVIN_TTS_OPENAI_KEY": "sk-tts-REAL",
        "GOOGLE_API_KEY": "goog-REAL",
        "OLLAMA_API_KEY": "olla-REAL",
        "GMAIL_APP_PASSWORD": "app-pw-REAL",
        "GMAIL_USER": "user@example.com",
        "HETZNER_API_TOKEN": "hz-REAL",
        "CORVIN_LICENSE_KEY": "CORVIN-REAL",
        "ANTHROPIC_BASE_URL": "http://evil.internal/",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)

    captured, _ = _run_worker_capture(monkeypatch)
    env = captured["env"]
    assert env is not None
    for k in secrets:
        assert k not in env, f"secret {k} must not reach the worker subprocess"
    # Non-secret vars in the real env stay intact on the copy.
    assert env.get("VOICE_HOOK_RECURSION") == "1"


def test_h2_strip_happens_before_provider_redirect(monkeypatch):
    """Invariant: creds are gone by the time _apply_provider_redirect sees env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-SECRET")
    seen = {}
    monkeypatch.setattr(_rt, "_resolve_worker_engine",
                        lambda model, tenant_id=None: ("claude_code", model))
    monkeypatch.setattr(_rt, "_assert_engine_licensed", lambda *a, **k: None)
    monkeypatch.setattr(_rt, "_claude_binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(_rt.shutil, "which", lambda *a, **k: "/usr/bin/claude")

    def _spy_redirect(env, tid):
        seen["has_key_at_redirect"] = "ANTHROPIC_API_KEY" in env

    monkeypatch.setattr(_rt, "_apply_provider_redirect", _spy_redirect)

    class _FakePopen:
        def __init__(self, *a, **k):
            pass

        def communicate(self, input=None, timeout=None):
            return ("", "")

        def poll(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(_rt.subprocess, "Popen", _FakePopen)
    _rt._call_worker_sync("t", "s", "claude-sonnet-4-6",
                          {"timeout_seconds": 30, "max_worker_turns": 20})
    assert seen.get("has_key_at_redirect") is False


# ---------------------------------------------------------------------------
# M9 — locality classification
# ---------------------------------------------------------------------------

def test_m9_engine_locality_claude_is_us_cloud():
    assert _rt._engine_locality("claude_code") == "us_cloud"


def test_m9_engine_locality_hermes_is_local():
    assert _rt._engine_locality("hermes") == "local"


def test_m9_engine_locality_unknown_defaults_us_cloud():
    # Safe direction: never under-claim US cloud processing as EU.
    assert _rt._engine_locality("some_unknown_engine") == "us_cloud"


def test_m9_worker_attestation_locality_us_cloud(monkeypatch):
    _, attestation = _run_worker_capture(monkeypatch, engine_id="claude_code")
    assert attestation["locality"] == "us_cloud"
    assert attestation["locality"] != "eu_cloud"


def test_m9_canonical_registry_agrees():
    """Guard against drift from the L34 compliance SSOT."""
    from data_classification import DEFAULT_ENGINE_COMPLIANCE
    assert DEFAULT_ENGINE_COMPLIANCE["claude_code"].locality == "us_cloud"
