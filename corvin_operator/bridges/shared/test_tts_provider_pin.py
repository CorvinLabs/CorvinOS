#!/usr/bin/env python3
"""test_tts_provider_pin.py — ADR-0883: the bridge adapter honours the TTS
provider pin, and a pinned cloud provider never falls back to the OTHER cloud.

Background (2026-09-18): say.py and the console honoured
``CORVIN_TTS_PROVIDER`` / ``profile.tts_provider``; ``adapter.py`` — the one
caller behind every Discord/WhatsApp/Email voice summary — ran a hard-wired
``openai → edge → piper`` chain. When a credential rotation left a placeholder
OpenAI key in ``service.env`` and a reboot loaded it, every voice summary
silently shipped to Microsoft edge-tts for a full day while
``docs/bridge-setup.md`` claimed the pin worked on this path.

Variant A (operator decision): pin ``openai`` → OpenAI, else LOCAL Piper,
else text-only. edge-tts is never attempted under an ``openai`` pin.

These tests drive the real ``synthesize_voice_note`` orchestrator with the
three provider tiers mocked — the orchestration is the unit under test.
The end-to-end proof (real Discord turn → journal shows the OpenAI tier
produced the OGG) is recorded in ADR-0883.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore  # noqa: E402

FAKE_OGG = ROOT / "outbox" / "does-not-need-to-exist.ogg"


@pytest.fixture(autouse=True)
def _clean_pin_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CORVIN_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    adapter._tts_pin_warned.clear()
    yield


def _profile_with(provider):
    """Stand-in for the profile module: only ``load()`` is consulted."""
    m = mock.Mock()
    m.load.return_value = {"tts_provider": provider} if provider is not None else {}
    return m


# ── chain shape ────────────────────────────────────────────────────────


def test_auto_chain_matches_say_py() -> None:
    """Parity guard: the no-pin chain is the same tuple say.py runs."""
    say_py = ROOT.parent.parent / "voice" / "scripts" / "say.py"
    src = say_py.read_text(encoding="utf-8")
    import re
    m = re.search(r"_AUTO_CHAIN\s*(?::[^=]+)?=\s*\(([^)]*)\)", src)
    assert m, "say.py::_AUTO_CHAIN not found"
    names = tuple(x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip())
    assert adapter._tts_chain_for(None) == names == ("openai", "edge", "piper")


@pytest.mark.parametrize(
    "pin, expected",
    [
        (None, ("openai", "edge", "piper")),
        ("openai", ("openai", "piper")),
        ("edge", ("edge", "piper")),
        ("piper", ("piper",)),
    ],
)
def test_chain_for_pin_never_crosses_clouds(pin, expected) -> None:
    assert adapter._tts_chain_for(pin) == expected


# ── pin resolution precedence ──────────────────────────────────────────


def test_env_pin_beats_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "piper")
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")):
        assert adapter._resolve_tts_provider() == "piper"


def test_profile_pin_used_when_env_unset() -> None:
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")):
        assert adapter._resolve_tts_provider() == "openai"


def test_env_auto_disables_profile_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "auto")
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")):
        assert adapter._resolve_tts_provider() is None


def test_unknown_pin_degrades_to_auto_and_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORVIN_TTS_PROVIDER", "elevenlabs")
    with mock.patch.object(adapter, "log") as m_log:
        assert adapter._resolve_tts_provider() is None
        assert adapter._resolve_tts_provider() is None
    assert m_log.call_count == 1


def test_no_profile_module_means_auto() -> None:
    with mock.patch.object(adapter, "_voice_profile", None):
        assert adapter._resolve_tts_provider() is None


# ── orchestration through the real synthesize_voice_note ───────────────


def test_openai_pin_skips_edge_and_uses_local_piper() -> None:
    """The load-bearing Variant-A assertion: OpenAI fails → Piper, edge NEVER called."""
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")), \
         mock.patch.object(adapter, "_try_openai_tts", return_value=None) as m_openai, \
         mock.patch.object(adapter, "_try_edge_tts", return_value=FAKE_OGG) as m_edge, \
         mock.patch.object(adapter, "_try_piper_tts", return_value=FAKE_OGG) as m_piper:
        result = adapter.synthesize_voice_note("Hallo.", lang="de")
    assert result == FAKE_OGG
    m_openai.assert_called_once()
    m_edge.assert_not_called()
    m_piper.assert_called_once()
    assert adapter.voice_skip_reason() is None


def test_openai_pin_success_touches_nothing_else() -> None:
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")), \
         mock.patch.object(adapter, "_try_openai_tts", return_value=FAKE_OGG) as m_openai, \
         mock.patch.object(adapter, "_try_edge_tts", return_value=FAKE_OGG) as m_edge, \
         mock.patch.object(adapter, "_try_piper_tts", return_value=FAKE_OGG) as m_piper:
        assert adapter.synthesize_voice_note("Hallo.", lang="de") == FAKE_OGG
    m_openai.assert_called_once()
    m_edge.assert_not_called()
    m_piper.assert_not_called()


def test_openai_pin_all_fail_names_the_pin_in_skip_reason() -> None:
    with mock.patch.object(adapter, "_voice_profile", _profile_with("openai")), \
         mock.patch.object(adapter, "_try_openai_tts", return_value=None), \
         mock.patch.object(adapter, "_try_edge_tts", return_value=FAKE_OGG) as m_edge, \
         mock.patch.object(adapter, "_try_piper_tts", return_value=None):
        assert adapter.synthesize_voice_note("Hallo.", lang="de") is None
    m_edge.assert_not_called()
    reason = adapter.voice_skip_reason()
    assert reason and "pinned to 'openai'" in reason


def test_piper_pin_is_local_only() -> None:
    with mock.patch.object(adapter, "_voice_profile", _profile_with("piper")), \
         mock.patch.object(adapter, "_try_openai_tts", return_value=FAKE_OGG) as m_openai, \
         mock.patch.object(adapter, "_try_edge_tts", return_value=FAKE_OGG) as m_edge, \
         mock.patch.object(adapter, "_try_piper_tts", return_value=None) as m_piper:
        assert adapter.synthesize_voice_note("Hallo.", lang="de") is None
    m_openai.assert_not_called()
    m_edge.assert_not_called()
    m_piper.assert_called_once()


def test_no_pin_keeps_historical_auto_chain() -> None:
    """Regression guard for installs without a pin: openai → edge → piper."""
    calls: list[str] = []
    with mock.patch.object(adapter, "_voice_profile", _profile_with(None)), \
         mock.patch.object(adapter, "_try_openai_tts", side_effect=lambda *a: calls.append("openai")), \
         mock.patch.object(adapter, "_try_edge_tts", side_effect=lambda *a: calls.append("edge")), \
         mock.patch.object(adapter, "_try_piper_tts", side_effect=lambda *a: calls.append("piper")):
        assert adapter.synthesize_voice_note("Hallo.", lang="de") is None
    assert calls == ["openai", "edge", "piper"]


def test_pin_is_reachable_from_the_turn_path() -> None:
    """Reachability: the per-turn caller (_synthesize_voice_for_turn) reaches
    synthesize_voice_note, which consults the pin — no second, unpinned
    synth path exists in the adapter."""
    import inspect
    src = inspect.getsource(adapter._synthesize_voice_for_turn)
    assert "synthesize_voice_note(" in src
    orch = inspect.getsource(adapter.synthesize_voice_note)
    assert "_resolve_tts_provider()" in orch and "_tts_chain_for(" in orch
    # No other function may call the tiers directly (bypassing the pin).
    whole = Path(adapter.__file__).read_text(encoding="utf-8")
    for tier in ("_try_openai_tts(", "_try_edge_tts(", "_try_piper_tts("):
        callers = [ln for ln in whole.splitlines()
                   if tier in ln and not ln.lstrip().startswith(("def ", "#", "return _try"))
                   and "mock" not in ln]
        assert callers == [], f"{tier} called outside _run_tts_provider: {callers}"


def test_quota_429_logs_once_per_backoff_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credits-exhausted 429 must leave ONE content-free trace in the log —
    not one per turn (spam), not zero (the 2026-09-18 blind spot)."""
    import types

    class _Speech:
        def create(self, **kw):
            raise RuntimeError("Error code: 429 - insufficient_quota credit_balance_exhausted")

    class _Client:
        def __init__(self, **kw):
            self.audio = types.SimpleNamespace(speech=_Speech())

    fake_openai = types.SimpleNamespace(OpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(adapter._provider_keys, "resolve_key", lambda k: "sk-test-key")
    with adapter._voice_engine_lock:
        adapter._voice_engine_state["quota_until"] = 0.0
        adapter._voice_engine_state["first_quota_logged"] = False
    with mock.patch.object(adapter, "log") as m_log:
        assert adapter._try_openai_tts("Hallo.", "de", None) is None
        assert adapter._try_openai_tts("Hallo.", "de", None) is None  # inside backoff
    quota_lines = [c for c in m_log.call_args_list if "quota" in str(c)]
    assert len(quota_lines) == 1, m_log.call_args_list
    with adapter._voice_engine_lock:
        adapter._voice_engine_state["quota_until"] = 0.0
        adapter._voice_engine_state["first_quota_logged"] = False
