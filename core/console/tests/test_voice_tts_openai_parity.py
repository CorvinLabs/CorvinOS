"""Parity contract for the console's in-process OpenAI TTS branch
(``routes/voice.py::_try_openai_tts`` / ``_voice_tts_sync``).

Adversarial review 2026-07-19: the OpenAI-first branch in ``_voice_tts_sync``
(a) ignored ``CORVIN_TTS_LOCAL_ONLY=1`` — the adapter's twin honours it
    (``adapter.py::_try_openai_tts``), so under EU local-only deployments the
    console shipped reply text to OpenAI's cloud (L35 violation);
(b) never used ``_OPENAI_TTS_TIMEOUT_S`` — SDK defaults (600 s, retries)
    could park a threadpool worker for minutes;
(c) ran BEFORE ``_summarize_for_speech`` / ``_resolve_tts_provider`` /
    ``_resolve_tts_voice`` — it spoke up to 4000 chars of raw markdown
    verbatim, overrode a pinned ``tts_provider`` (a piper pin still went to
    the cloud) and hardcoded voice="nova".

These tests pin the fixed pipeline: summarize → resolve provider + voice →
OpenAI in-process only when the resolved provider is OpenAI → say.py fallback.
Every OpenAI touchpoint is faked (no network, no real key needed).
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "abcd1234"


class _FakeSpeeches:
    """Records every ``audio.speech.create(**kwargs)`` call."""

    def __init__(self, store):
        self._store = store

    def create(self, **kwargs):
        self._store["create_kwargs"] = kwargs
        return types.SimpleNamespace(content=b"OggS" + b"\x00" * 16)


class _FakeOpenAIFactory:
    """Stand-in for ``openai.OpenAI`` recording constructor kwargs."""

    def __init__(self, store):
        self._store = store

    def __call__(self, **kwargs):
        self._store["ctor_kwargs"] = kwargs
        client = types.SimpleNamespace()
        client.audio = types.SimpleNamespace(speech=_FakeSpeeches(self._store))
        return client


@pytest.fixture()
def fake_openai(monkeypatch):
    """Inject a fake ``openai`` module + a fake resolved key; capture calls."""
    store: dict = {}
    fake_module = types.SimpleNamespace(OpenAI=_FakeOpenAIFactory(store))
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    import provider_keys as _pk  # bridges/shared, on sys.path via routes.voice
    monkeypatch.setattr(_pk, "resolve_key",
                        lambda name: "sk-test-fake" if name == "tts_openai_api_key" else None)
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    return store


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr=stderr)


# ── (1) CORVIN_TTS_LOCAL_ONLY=1 disables the cloud branch entirely ──────────

def test_local_only_skips_openai_without_touching_the_client(monkeypatch):
    """L35 parity with adapter.py:_try_openai_tts — under the EU local-only
    egress guarantee the in-process branch must return None BEFORE any
    key resolution or client construction (no network, ever)."""
    class _Exploding:
        def __getattr__(self, name):  # any attribute access = client touched
            raise AssertionError("OpenAI client must not be touched under "
                                 "CORVIN_TTS_LOCAL_ONLY=1")

    monkeypatch.setitem(sys.modules, "openai", _Exploding())
    import provider_keys as _pk

    def _no_key_lookup(name):
        raise AssertionError("key must not be resolved under local-only")

    monkeypatch.setattr(_pk, "resolve_key", _no_key_lookup)
    monkeypatch.setenv("CORVIN_TTS_LOCAL_ONLY", "1")

    assert V._try_openai_tts("hallo", "de", "nova") is None


def test_local_only_pipeline_falls_through_to_say_py(monkeypatch, fake_openai):
    """Full-pipeline check: with local-only set, /voice/tts synthesizes via
    the say.py subprocess chain (which enforces local-only itself)."""
    monkeypatch.setenv("CORVIN_TTS_LOCAL_ONLY", "1")
    say_seen = {}

    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "strip_for_tts.py":
            # Code pre-strip (_summarize_for_speech runs it before summarize.py) —
            # pass the text through unchanged.
            return _completed(0, stdout=kwargs.get("input", "") or "")
        if Path(cmd[1]).name == "summarize.py":
            return _completed(0, stdout="Kurze Zusammenfassung.")
        if Path(cmd[1]).name == "say.py":
            out_path = Path(cmd[2])
            say_seen["text"] = cmd[3]
            out_path.write_bytes(b"RIFF" + b"\x00" * 4 + b"WAVEfake")
            return _completed(0, stdout=str(out_path))
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: None)
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    resp = V._voice_tts_sync(V.TtsRequest(text="Eine Antwort. " * 50, lang="de"),
                             rec=_FakeRec())

    assert resp.status_code == 200
    assert resp.headers["X-Corvin-TTS-Provider"] == "say.py"
    assert "create_kwargs" not in fake_openai, "OpenAI must never be called"
    assert say_seen["text"] == "Kurze Zusammenfassung."


# ── (2) OpenAI receives the SUMMARIZED text + RESOLVED voice ────────────────

def test_openai_receives_summary_and_resolved_voice_not_raw_text(monkeypatch, fake_openai):
    """The core parity regression: the in-process branch must speak the same
    condensed summary (and profile voice) the say.py chain would — never the
    raw 4000-char answer with a hardcoded nova."""
    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "strip_for_tts.py":
            # Code pre-strip (_summarize_for_speech runs it before summarize.py) —
            # pass the text through unchanged.
            return _completed(0, stdout=kwargs.get("input", "") or "")
        if Path(cmd[1]).name == "summarize.py":
            return _completed(0, stdout="Kurze gesprochene Zusammenfassung.")
        raise AssertionError(f"say.py must not run when OpenAI succeeds: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: "openai")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: "shimmer")
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    raw = "Dies ist eine sehr lange Antwort voller Markdown. " * 100
    resp = V._voice_tts_sync(V.TtsRequest(text=raw, lang="de"), rec=_FakeRec())

    assert resp.status_code == 200
    assert resp.headers["X-Corvin-TTS-Provider"] == "openai"
    kwargs = fake_openai["create_kwargs"]
    assert kwargs["input"] == "Kurze gesprochene Zusammenfassung.", (
        "OpenAI must receive the CONDENSED summary, not the raw answer text")
    assert kwargs["voice"] == "shimmer", (
        "the profile-resolved voice must be honoured, not a hardcoded nova")


def test_unmapped_profile_voice_falls_back_to_nova(monkeypatch, fake_openai):
    """A profile voice that is NOT an OpenAI voice name (e.g. an edge neural
    voice) cannot be sent to OpenAI — fall back to nova, never send a doomed
    request."""
    monkeypatch.setattr(V, "_summarize_for_speech", lambda text, lang: "Kurz.")
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: "openai")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: "de-DE-KatjaNeural")
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    resp = V._voice_tts_sync(V.TtsRequest(text="Hallo Welt", lang="de"), rec=_FakeRec())

    assert resp.status_code == 200
    assert fake_openai["create_kwargs"]["voice"] == "nova"


# ── (3) a pinned non-OpenAI provider never reaches the OpenAI branch ────────

def test_pinned_piper_provider_never_reaches_openai(monkeypatch, fake_openai):
    """tts_provider=piper is a user pin — the request must go straight to
    say.py (which honours the pin), even with a valid OpenAI key present."""
    say_seen = {}

    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "strip_for_tts.py":
            # Code pre-strip (_summarize_for_speech runs it before summarize.py) —
            # pass the text through unchanged.
            return _completed(0, stdout=kwargs.get("input", "") or "")
        if Path(cmd[1]).name == "summarize.py":
            return _completed(0, stdout="Kurze Zusammenfassung.")
        if Path(cmd[1]).name == "say.py":
            out_path = Path(cmd[2])
            say_seen["argv"] = list(cmd)
            out_path.write_bytes(b"RIFF" + b"\x00" * 4 + b"WAVEfake")
            return _completed(0, stdout=str(out_path))
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: "piper")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    resp = V._voice_tts_sync(V.TtsRequest(text="Eine Antwort. " * 20, lang="de"),
                             rec=_FakeRec())

    assert resp.status_code == 200
    assert resp.headers["X-Corvin-TTS-Provider"] == "say.py"
    assert "ctor_kwargs" not in fake_openai, (
        "a piper pin must never construct an OpenAI client")
    assert "create_kwargs" not in fake_openai, (
        "a piper pin must never reach OpenAI's API")
    assert say_seen["argv"][-1] == "piper", "the pin must travel to say.py's argv"


def test_operator_env_pin_piper_never_reaches_openai(monkeypatch, fake_openai):
    """CORVIN_TTS_PROVIDER=piper is the OPERATOR pin (documented as final
    precedence). The in-process OpenAI branch runs BEFORE say.py, so the pin
    MUST be honoured by _resolve_tts_provider itself — otherwise a local pin
    silently ships reply text to OpenAI's cloud (compliance/L35). This test
    deliberately does NOT patch _resolve_tts_provider: it exercises the real
    env-var resolution."""
    say_seen = {}

    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "strip_for_tts.py":
            # Code pre-strip (_summarize_for_speech runs it before summarize.py) —
            # pass the text through unchanged.
            return _completed(0, stdout=kwargs.get("input", "") or "")
        if Path(cmd[1]).name == "summarize.py":
            return _completed(0, stdout="Kurze Zusammenfassung.")
        if Path(cmd[1]).name == "say.py":
            out_path = Path(cmd[2])
            say_seen["argv"] = list(cmd)
            out_path.write_bytes(b"RIFF" + b"\x00" * 4 + b"WAVEfake")
            return _completed(0, stdout=str(out_path))
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "piper")
    # No profile pin — the common case where the operator pins via env only.
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    resp = V._voice_tts_sync(V.TtsRequest(text="Eine Antwort. " * 20, lang="de"),
                             rec=_FakeRec())

    assert resp.status_code == 200
    assert "ctor_kwargs" not in fake_openai, (
        "CORVIN_TTS_PROVIDER=piper must never construct an OpenAI client")
    assert "create_kwargs" not in fake_openai, (
        "operator env pin piper was bypassed — text reached OpenAI's cloud")
    assert say_seen["argv"][-1] == "piper", "the env pin must travel to say.py's argv"


def test_operator_env_pin_auto_allows_openai(monkeypatch, fake_openai):
    """CORVIN_TTS_PROVIDER=auto means 'no pin' → the in-process OpenAI branch
    is allowed (regression guard so the env resolution doesn't over-block)."""
    monkeypatch.setattr(V.subprocess, "run",
                        lambda cmd, **k: _completed(0, stdout="Kurz."))
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "auto")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    resp = V._voice_tts_sync(V.TtsRequest(text="Hallo.", lang="de"), rec=_FakeRec())
    assert resp.status_code == 200
    assert "create_kwargs" in fake_openai, "auto (no pin) should allow OpenAI"


# ── (4) client construction: timeout pinned, retries off ────────────────────

def test_openai_client_is_constructed_with_timeout_and_no_retries(fake_openai):
    """Parity with adapter.py (timeout=15.0, max_retries=0 there): without
    these the SDK defaults to 600 s with retries — a degraded network parks
    the request thread in TTS for minutes before say.py is even attempted."""
    out = V._try_openai_tts("hallo welt", "de", "nova")

    assert out is not None
    ctor = fake_openai["ctor_kwargs"]
    assert ctor["timeout"] == V._OPENAI_TTS_TIMEOUT_S
    assert ctor["max_retries"] == 0


def test_openai_input_is_clamped_to_provider_char_limit(fake_openai):
    """OpenAI TTS-1 rejects >4096 chars — the branch clamps at the shared
    _TTS_PROVIDER_CHAR_LIMIT exactly like the say.py path does."""
    V._try_openai_tts("x" * 10000, "de", None)
    assert len(fake_openai["create_kwargs"]["input"]) == V._TTS_PROVIDER_CHAR_LIMIT


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
