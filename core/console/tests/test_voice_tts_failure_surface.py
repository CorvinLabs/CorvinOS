"""TTS failure surface + temp hygiene (review findings V1/V2b/V6, 2026-07-20).

V1  — the say.py ``TimeoutExpired`` path in ``voice_tts`` returned a bare 204
      WITHOUT the ``X-Corvin-Voice-Reason`` header, while every other degrade
      path goes through ``_tts_failed_response``. The one blind spot in the
      diagnostic surface was exactly the path a SIGKILLed say.py hits.

V2b — the route's ``finally`` unlinked only the ``.opus`` target. say.py's
      Piper tier synthesizes into ``out_path.with_suffix(".wav")`` before
      replacing it onto ``out_path``; when the console's outer timeout kills
      say.py mid-synthesis, that sibling survives and ``corvin_tts_*.wav``
      files accumulate in the tempdir.

V6  — in-process OpenAI TTS failures were logged at DEBUG only, so a
      permanently dead PAID tier was invisible. Now WARNING once per process
      (no per-turn spam), DEBUG afterwards, content-free (type + status only).
"""
from __future__ import annotations

import logging
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


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout,
                                       stderr=stderr)


def _run_tts_with_say_timeout(monkeypatch, paths: dict):
    """Drive _voice_tts_sync so say.py raises TimeoutExpired mid-synthesis,
    after having created the Piper .wav sibling next to its .opus target."""
    def _fake_run(cmd, **kwargs):
        name = Path(cmd[1]).name
        if name == "summarize.py":
            return _completed(0, stdout="Short spoken summary.")
        if name == "say.py":
            out_path = Path(cmd[2])
            wav = out_path.with_suffix(".wav")
            wav.write_bytes(b"RIFF-partial")  # piper mid-synthesis leftover
            paths["opus"] = out_path
            paths["wav"] = wav
            raise subprocess.TimeoutExpired(
                cmd=cmd, timeout=25,
                stderr="say.py: edge-tts failed: TimeoutError",
            )
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(V.subprocess, "run", _fake_run)
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: None)
    monkeypatch.setattr(V, "_try_openai_tts", lambda *a, **k: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)
    monkeypatch.setattr(V.console_audit, "action_failed", lambda **k: None)
    body = V.TtsRequest(text="hello world", lang="en")
    return V._voice_tts_sync(body, rec=_FakeRec())


# ── V1: timeout path must serve the reason surface ──────────────────────────

def test_v1_say_timeout_carries_reason_header(monkeypatch):
    resp = _run_tts_with_say_timeout(monkeypatch, {})
    assert resp.status_code == 204
    reason = resp.headers.get("X-Corvin-Voice-Reason", "")
    assert "timeout" in reason.lower(), (
        "the TimeoutExpired degrade path must carry X-Corvin-Voice-Reason "
        f"like every other failure path — got {reason!r}"
    )


# ── V2b: .wav sibling cleanup ───────────────────────────────────────────────

def test_v2b_wav_sibling_cleaned_up_on_timeout(monkeypatch):
    paths: dict = {}
    _run_tts_with_say_timeout(monkeypatch, paths)
    assert not paths["opus"].exists(), ".opus temp must be unlinked"
    assert not paths["wav"].exists(), (
        "the corvin_tts_*.wav sibling (piper mid-synthesis leftover) must be "
        "unlinked by the route's finally, not accumulate in the tempdir"
    )


def test_v2b_cleanup_helper_is_best_effort(tmp_path):
    p = tmp_path / "corvin_tts_x.opus"
    V._cleanup_tts_tmp(p)  # neither file exists — must not raise
    p.write_bytes(b"a")
    wav = tmp_path / "corvin_tts_x.wav"
    wav.write_bytes(b"b")
    V._cleanup_tts_tmp(p)
    assert not p.exists() and not wav.exists()


# ── V6: dead paid OpenAI tier must be visible (once) ────────────────────────

def _call_failing_openai_tts(monkeypatch):
    class _BoomClient:
        def __init__(self, **kw):
            raise RuntimeError("boom sk-SECRET spoken-prompt-text")

    monkeypatch.setitem(sys.modules, "openai",
                        types.SimpleNamespace(OpenAI=_BoomClient))
    monkeypatch.setitem(sys.modules, "provider_keys",
                        types.SimpleNamespace(resolve_key=lambda name: "sk-test"))
    monkeypatch.delenv("CORVIN_TTS_LOCAL_ONLY", raising=False)
    return V._try_openai_tts("some text", "en", None)


def test_v6_openai_failure_warns_once_then_debug(monkeypatch, caplog):
    monkeypatch.setattr(V, "_openai_tts_warned_once", False, raising=False)
    with caplog.at_level(logging.DEBUG, logger=V._log.name):
        assert _call_failing_openai_tts(monkeypatch) is None
        assert _call_failing_openai_tts(monkeypatch) is None
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1, (
        "in-process OpenAI TTS failure must WARN exactly once per process "
        f"(got {len(warns)} WARNINGs)"
    )
    # Content-free (compliance): never str(e) — it can embed the request
    # payload, i.e. the spoken text.
    assert "sk-SECRET" not in warns[0].getMessage()
    assert "spoken-prompt-text" not in warns[0].getMessage()
    assert "RuntimeError" in warns[0].getMessage()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
