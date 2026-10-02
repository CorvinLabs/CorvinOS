"""Invariant (session-drift analysis 2026-10-02, L5): a turn's spoken audio is
regenerable from what the append-only turn log keeps.

ADR-0194 binds voice audio to the session workdir: the per-turn archive is
pruned oldest-first (64 MB cap) and removed with the workdir. That is only
loss-free if the TEXT the audio was spoken from — and the key the audio is
filed under — survive in ``turns.jsonl``. (The ADR's ``voice/<turn_id>/
manifest.json`` was never built; the archive is content-addressed by
``voice_key(text)``, so the text IS the manifest.) This pins that: after the
audio is gone, the persisted turn still yields the exact key and text, and a
re-synthesised file under that key is found again by history rehydrate.
"""
from __future__ import annotations

import pytest

from corvin_console import chat_runtime as cr


@pytest.fixture()
def sess(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    return cr.create_session("_default", "voice-invariant")


def test_audio_is_regenerable_from_the_persisted_turn(sess):
    spoken = "Die Lieferung kommt am Freitag um zehn Uhr."
    key = cr.voice_key(spoken)
    cr._append_turn(sess, "user", [{"kind": "text", "text": "Wann kommt die Lieferung?"}])
    cr._append_turn(sess, "assistant", [{"kind": "text", "text": spoken}], voice_key_hint=key)

    vdir = cr.voice_dir("_default", sess.sid)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{key}.mp3").write_bytes(b"ID3fake-audio")
    turns = cr.attach_voice_artifacts("_default", sess.sid, cr.read_turns("_default", sess.sid))
    assert any(str(p.get("label", "")).startswith("voice") for p in turns[-1]["parts"])

    # The audio is lost (prune / workdir teardown) …
    for f in vdir.iterdir():
        f.unlink()
    turns = cr.read_turns("_default", sess.sid)
    last = turns[-1]
    # … but the log still carries the exact text and the exact key to file it under.
    assert cr._turn_text(last) == spoken
    assert last["voice_key"] == key == cr.voice_key(cr._turn_text(last))
    rehydrated = cr.attach_voice_artifacts("_default", sess.sid, turns)
    assert not any(str(p.get("label", "")).startswith("voice") for p in rehydrated[-1]["parts"])

    # Re-synthesising from the persisted text lands where rehydrate looks.
    (vdir / f"{cr.voice_key(cr._turn_text(last))}.ogg").write_bytes(b"OggSfake")
    again = cr.attach_voice_artifacts("_default", sess.sid, cr.read_turns("_default", sess.sid))
    assert any(str(p.get("label", "")).startswith("voice") for p in again[-1]["parts"])


def test_key_is_whitespace_stable(sess):
    assert cr.voice_key("a  b\n") == cr.voice_key(" a b")
