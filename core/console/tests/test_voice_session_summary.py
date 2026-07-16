"""POST /v1/console/voice/session-summary — a spoken recap of a WHOLE chat
session (goal / method / current state), not one turn. User-requested
feature: a button next to the voice-replay controls that regenerates a
freshly-worded recap on every press (a rotating "angle" is passed to
summarize.py's --session-recap-mode so repeat presses don't come back
identical).

Deliberately NOT archived (no _persist_turn_voice call) — there is no stable
source text to key an archive slot by, since the whole point is that the
same session produces different wording on every call.
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


def _turn(role, text):
    return {"role": role, "ts": 0.0, "parts": [{"kind": "text", "text": text}]}


# ── _build_session_transcript ────────────────────────────────────────────────

def test_build_session_transcript_joins_user_and_assistant_turns(monkeypatch):
    turns = [_turn("user", "Baue mir X"), _turn("assistant", "X ist fertig.")]
    from corvin_console import chat_runtime as _cr
    monkeypatch.setattr(_cr, "read_turns", lambda *a, **k: turns)
    out = V._build_session_transcript("_default", "sid1")
    assert "User: Baue mir X" in out
    assert "Assistant: X ist fertig." in out


def test_build_session_transcript_empty_when_no_turns(monkeypatch):
    from corvin_console import chat_runtime as _cr
    monkeypatch.setattr(_cr, "read_turns", lambda *a, **k: [])
    assert V._build_session_transcript("_default", "sid1") == ""


def test_build_session_transcript_skips_non_text_and_system_turns(monkeypatch):
    turns = [
        {"role": "system", "ts": 0.0, "parts": [{"kind": "text", "text": "ignored"}]},
        _turn("user", "echte Frage"),
        {"role": "assistant", "ts": 0.0, "parts": [{"kind": "tool", "name": "Bash"}]},
    ]
    from corvin_console import chat_runtime as _cr
    monkeypatch.setattr(_cr, "read_turns", lambda *a, **k: turns)
    out = V._build_session_transcript("_default", "sid1")
    assert "ignored" not in out
    assert "echte Frage" in out


def test_build_session_transcript_keeps_head_and_tail_when_over_budget(monkeypatch):
    turns = [_turn("user", "GOAL-MARKER erstes Ziel"), _turn("assistant", "ok, verstanden")]
    # Pad the middle with turns that must be dropped once the budget is exceeded.
    for i in range(200):
        turns.append(_turn("user", f"Zwischenschritt {i} " * 5))
        turns.append(_turn("assistant", f"Antwort {i} " * 5))
    turns.append(_turn("user", "TAIL-MARKER letzter Stand"))
    from corvin_console import chat_runtime as _cr
    monkeypatch.setattr(_cr, "read_turns", lambda *a, **k: turns)
    out = V._build_session_transcript("_default", "sid1", budget=2000)
    assert len(out) <= 2000 + 50  # small slack for the "[...]" marker
    assert "GOAL-MARKER" in out
    assert "TAIL-MARKER" in out
    assert "[...]" in out


# ── /voice/session-summary route ────────────────────────────────────────────

class _FakeSession:
    pass


def _patch_common(monkeypatch, *, session_exists=True, turns=None):
    from corvin_console import chat_runtime as _cr
    monkeypatch.setattr(_cr, "get_session",
                        lambda tid, sid: (_FakeSession() if session_exists else None))
    monkeypatch.setattr(_cr, "read_turns", lambda *a, **k: turns or [])
    from corvin_console.routes import _compute_license_gate as _gate
    monkeypatch.setattr(_gate, "enforce_voice_summaries", lambda *a, **k: None)


def _fake_rec():
    rec = mock.Mock()
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "fp123"
    return rec


def test_session_summary_204_when_session_missing(monkeypatch):
    _patch_common(monkeypatch, session_exists=False)
    body = V.SessionSummaryRequest(sid="doesnotexist", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())
    assert resp.status_code == 204


def test_session_summary_204_when_no_turns(monkeypatch):
    _patch_common(monkeypatch, turns=[])
    body = V.SessionSummaryRequest(sid="sid1", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())
    assert resp.status_code == 204


def test_session_summary_passes_rotating_angle_and_session_recap_flag(monkeypatch):
    turns = [_turn("user", "Ziel A"), _turn("assistant", "erreicht")]
    _patch_common(monkeypatch, turns=turns)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--session-recap-mode" in cmd:
            return _completed(0, stdout="Kurzer Recap-Text.\n")
        return _completed(0, stdout="AUDIO_STDOUT_MARKER")  # say.py's own stdout is unused by this path

    monkeypatch.setattr(V.subprocess, "run", fake_run)
    monkeypatch.setattr(V, "_say_cmd", lambda out_path, text, lang: ["echo", text])
    monkeypatch.setattr(Path, "stat", lambda self: mock.Mock(st_size=100))
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"\x89PNG" + b"0" * 100)  # any nonzero bytes
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)

    body = V.SessionSummaryRequest(sid="sid1", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())

    assert resp.status_code == 200
    summarize_call = next(c for c in calls if "--session-recap-mode" in c)
    assert "--angle" in summarize_call
    angle_idx = summarize_call.index("--angle") + 1
    assert summarize_call[angle_idx] in V._SESSION_RECAP_ANGLES_DE


def test_session_summary_204_when_summarizer_fails(monkeypatch):
    turns = [_turn("user", "Ziel A"), _turn("assistant", "erreicht")]
    _patch_common(monkeypatch, turns=turns)
    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: _completed(1, stderr="boom"))
    body = V.SessionSummaryRequest(sid="sid1", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())
    assert resp.status_code == 204


def test_session_summary_204_on_timeout(monkeypatch):
    turns = [_turn("user", "Ziel A"), _turn("assistant", "erreicht")]
    _patch_common(monkeypatch, turns=turns)

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["fake"], timeout=120)
    monkeypatch.setattr(V.subprocess, "run", _raise)
    body = V.SessionSummaryRequest(sid="sid1", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())
    assert resp.status_code == 204


def test_session_summary_does_not_archive(monkeypatch):
    """No _persist_turn_voice call anywhere in this path — a session recap
    has no stable source text to key an archive slot by (see the route's
    own docstring). A regression that adds archiving here would silently
    either collide across clicks or need a whole new archive/erasure
    scheme this lightweight feature doesn't need."""
    turns = [_turn("user", "Ziel A"), _turn("assistant", "erreicht")]
    _patch_common(monkeypatch, turns=turns)
    called = {"persisted": False}

    def _fake_persist(*a, **k):
        called["persisted"] = True
        return None
    monkeypatch.setattr(V, "_persist_turn_voice", _fake_persist)

    def fake_run(cmd, **kwargs):
        if "--session-recap-mode" in cmd:
            return _completed(0, stdout="Kurzer Recap-Text.\n")
        return _completed(0, stdout="ok")

    monkeypatch.setattr(V.subprocess, "run", fake_run)
    monkeypatch.setattr(V, "_say_cmd", lambda out_path, text, lang: ["echo", text])
    monkeypatch.setattr(Path, "stat", lambda self: mock.Mock(st_size=100))
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"\x89PNG" + b"0" * 100)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)

    body = V.SessionSummaryRequest(sid="sid1", lang="de")
    resp = V._voice_session_summary_sync(body, _fake_rec())
    assert resp.status_code == 200
    assert called["persisted"] is False
