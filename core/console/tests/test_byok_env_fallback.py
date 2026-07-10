"""WA-20/WA-22: /byok/secrets must reflect a key configured the way this
codebase's own docs tell users to configure it (an env var, or a line in
~/.config/corvin-voice/service.env) — not only the opt-in BYOK vault that
nothing writes to by default. Since WA-22, presence is checked via the
single canonical resolver (operator/bridges/shared/provider_keys.py), the
same one say.py / stt/openai_whisper.py / BYOK's write path all use.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import byok as B


class _FakeRec:
    tenant_id = "_default"


def _empty_vault(monkeypatch, tmp_path):
    """Point both the vault module and byok's own file-fallback lookup at an
    empty, isolated XDG config dir so neither sees any real local state."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_HOSTED_MODE", raising=False)


def test_env_var_makes_a_key_present_even_with_empty_vault(monkeypatch, tmp_path):
    _empty_vault(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")

    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["anthropic_api_key"] is True


def test_stt_key_resolves_through_the_same_precedence_as_the_real_provider(
    monkeypatch, tmp_path,
):
    """CORVIN_STT_OPENAI_KEY (what stt/openai_whisper.py actually reads) must
    register as present — a general OPENAI_API_KEY alone does too (documented
    fallback), but a TTS-only CORVIN_TTS_OPENAI_KEY must NOT satisfy the STT
    slot (that's a different provider's key, per say.py vs openai_whisper.py)."""
    _empty_vault(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CORVIN_STT_OPENAI_KEY", raising=False)
    monkeypatch.setenv("CORVIN_TTS_OPENAI_KEY", "sk-tts-only")

    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["stt_openai_api_key"] is False

    monkeypatch.setenv("CORVIN_STT_OPENAI_KEY", "sk-stt-value")
    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["stt_openai_api_key"] is True


def test_service_env_file_is_checked_not_just_process_env(monkeypatch, tmp_path):
    """The key may be configured directly in ~/.config/corvin-voice/service.env
    without ever being exported into this process's environment — the real
    resolvers fall back to reading the file, and so must this presence check."""
    _empty_vault(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CORVIN_STT_OPENAI_KEY", raising=False)
    voice_dir = tmp_path / "corvin-voice"
    voice_dir.mkdir(parents=True)
    (voice_dir / "service.env").write_text(
        "CORVIN_STT_OPENAI_KEY=sk-from-file\n", encoding="utf-8",
    )

    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["stt_openai_api_key"] is True


def test_tts_only_key_no_longer_satisfies_the_generic_openai_slot(monkeypatch, tmp_path):
    """WA-22: pre-consolidation, a TTS-scoped CORVIN_TTS_OPENAI_KEY also
    satisfied the generic "openai_api_key" BYOK presence check — a
    cross-contamination the audit flagged, since TTS is a *fallback
    consumer* of the general key, not the other way around. The generic
    slot must now only reflect a real OPENAI_API_KEY (or its legacy
    OPENAI_APIKEY alias)."""
    _empty_vault(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_APIKEY", raising=False)
    monkeypatch.setenv("CORVIN_TTS_OPENAI_KEY", "sk-tts-only")

    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["openai_api_key"] is False


def test_hosted_mode_does_not_use_the_local_env_fallback(monkeypatch, tmp_path):
    """The env/file fallback reads LOCAL process state — meaningless (and
    potentially misleading) when this console is a thin proxy to a remote
    Management API in hosted mode."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_HOSTED_MODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-local-only")
    monkeypatch.setattr(B, "_agent_get", lambda path, timeout=None: {"ok": True, "keys": []})

    result = B.list_secrets(rec=_FakeRec())
    present = {k["key_name"]: k["present"] for k in result["keys"]}
    assert present["anthropic_api_key"] is False
