"""POST /v1/console/voice/tts now speaks a real, condensed summary instead
of the raw answer text — closing the console/messenger-bridge parity gap
found 2026-07-14 ("voice summary works via Discord, not in the console
chat"). Every messenger bridge speaks a summary via
``adapter.py::build_voice_summary()`` (which calls ``summarize.py``); the
console's ``/voice/tts`` used to speak ``body.text`` truncated blindly at
4000 chars, and the fully-working ``/voice/summarize`` endpoint was never
called from the frontend. This is now closed server-side (no frontend
change needed): ``voice_tts()`` calls the new ``_summarize_for_speech()``
helper before invoking ``say.py``, falling back to the raw truncated text
only if summarization is unavailable or fails.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr=stderr)


# ── _summarize_for_speech ───────────────────────────────────────────────────

def test_summarize_for_speech_returns_summarizer_output(monkeypatch):
    monkeypatch.setattr(
        V.subprocess, "run",
        lambda *a, **k: _completed(0, stdout="Kurze Zusammenfassung.\n"),
    )
    out = V._summarize_for_speech("ein langer Text " * 100, "de")
    assert out == "Kurze Zusammenfassung."


def test_summarize_for_speech_returns_none_on_timeout(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=120)
    monkeypatch.setattr(V.subprocess, "run", _raise)
    assert V._summarize_for_speech("text", "de") is None


def test_summarize_for_speech_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        V.subprocess, "run",
        lambda *a, **k: _completed(1, stderr="boom"),
    )
    assert V._summarize_for_speech("text", "de") is None


def test_summarize_for_speech_returns_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: _completed(0, stdout="   "))
    assert V._summarize_for_speech("text", "de") is None


def test_summarize_for_speech_returns_none_when_script_missing(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert V._summarize_for_speech("text", "de") is None


def test_summarize_for_speech_logs_degraded_sentinel(monkeypatch, caplog):
    monkeypatch.setattr(
        V.subprocess, "run",
        lambda *a, **k: _completed(
            0, stdout="near-verbatim passthrough",
            stderr="[summarize] degraded: both LLM backends unavailable",
        ),
    )
    with caplog.at_level("INFO", logger=V._log.name):
        out = V._summarize_for_speech("text", "de")
    assert out == "near-verbatim passthrough"
    assert any("degraded" in rec.message for rec in caplog.records)


# ── voice_tts() wiring ───────────────────────────────────────────────────────

class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "abcd1234"


def test_voice_tts_speaks_the_summary_not_the_raw_text(monkeypatch):
    """The core regression test: say.py must receive the CONDENSED summary
    as its text argv, not body.text verbatim."""
    summarize_calls = []

    captured_say_text = {}

    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "summarize.py":
            summarize_calls.append(cmd)
            return _completed(0, stdout="Kurze gesprochene Zusammenfassung.")
        if Path(cmd[1]).name == "say.py":
            # say.py argv: [sys.executable, say_path, out_path, text, lang, ...]
            out_path = Path(cmd[2])
            captured_say_text["text"] = cmd[3]
            out_path.write_bytes(b"RIFFfakeaudio")
            return _completed(0)
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    long_answer = "Dies ist eine sehr lange Antwort. " * 50
    body = V.TtsRequest(text=long_answer, lang="de")

    resp = V.voice_tts(body, rec=_FakeRec())

    assert resp.status_code == 200
    assert len(summarize_calls) == 1, "summarize.py must be invoked before say.py"
    assert captured_say_text["text"] == "Kurze gesprochene Zusammenfassung.", (
        "say.py must receive the CONDENSED summary, not the raw answer text"
    )


def test_voice_tts_falls_back_to_raw_truncated_text_when_summarize_fails(monkeypatch):
    """If summarization is unavailable, the OLD behavior (raw text, blindly
    truncated to the provider limit) must still work — TTS must never break
    because the summarizer failed."""
    captured_say_text = {}

    def _fake_run(cmd, **kwargs):
        if Path(cmd[1]).name == "summarize.py":
            return _completed(1, stderr="boom")  # summarizer fails
        if Path(cmd[1]).name == "say.py":
            out_path = Path(cmd[2])
            captured_say_text["text"] = cmd[3]
            out_path.write_bytes(b"RIFFfakeaudio")
            return _completed(0)
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    raw_text = "x" * 5000  # over the 4000-char provider limit
    body = V.TtsRequest(text=raw_text, lang="de")

    resp = V.voice_tts(body, rec=_FakeRec())

    assert resp.status_code == 200
    assert captured_say_text["text"] == raw_text[:V._TTS_PROVIDER_CHAR_LIMIT]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
