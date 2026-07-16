"""ADR-0194 Phase 1 — per-turn voice archive (persist + rehydrate as a player).

The spoken audio for a turn already existed server-side: /voice/tts summarises the
reply, shells out to say.py (which WRITES an audio file), returns the bytes — and
then deleted the file. Phase 1 keeps that file inside the session workdir, which
turns it into an ordinary chat artifact: served inline, rendered by ArtifactCard as
a real <audio> player, rehydrated on reload, erased with the session.

Two invariants carry the design and are pinned here:
  * the writer (/voice/tts) and the reader (history rehydrate) share no turn id, so
    they meet on a hash of the NORMALISED turn text;
  * say.py does NOT transcode (OGG-Opus / MP3 / WAV depending on which provider
    won), so the archived extension must follow the SNIFFED mime, never a
    hard-coded ".opus".
"""
from __future__ import annotations

from pathlib import Path

from corvin_console import chat_runtime as cr
from corvin_console.routes import voice as voice_routes


# ── voice_key: the writer/reader meeting point ───────────────────────────────

def test_voice_key_is_whitespace_normalised() -> None:
    """The streamed `result` text and the persisted `combined_text` differ in
    whitespace/trailing newlines — that must NOT change the key, or the player
    silently never re-appears after a reload."""
    assert cr.voice_key("Hallo  Welt\n") == cr.voice_key("Hallo Welt")
    assert cr.voice_key(" a\n\nb ") == cr.voice_key("a b")


def test_voice_key_differs_for_different_text() -> None:
    assert cr.voice_key("Hallo Welt") != cr.voice_key("Hallo Mond")


def test_voice_key_handles_empty() -> None:
    assert cr.voice_key("") == cr.voice_key("   ")  # no crash, stable


# ── attach_voice_artifacts: read-time rehydration ────────────────────────────

def _mk_turn(role: str, text: str) -> dict:
    return {"role": role, "ts": 1.0, "parts": [{"kind": "text", "text": text}]}


def _seed_voice(tmp_path, monkeypatch, sid: str, text: str, ext: str = ".ogg") -> Path:
    """Place an audio file where the archive expects it for `text`."""
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / s)
    vdir = tmp_path / sid / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    f = vdir / f"{cr.voice_key(text)}{ext}"
    f.write_bytes(b"OggS" + b"\x00" * 64)
    return f


def test_assistant_turn_gets_its_voice_artifact(tmp_path, monkeypatch) -> None:
    text = "Die Antwort auf alles."
    _seed_voice(tmp_path, monkeypatch, "s1", text)
    turns = [_mk_turn("user", "frage"), _mk_turn("assistant", text)]
    out = cr.attach_voice_artifacts("_default", "s1", turns)
    art = [p for p in out[1]["parts"] if p.get("kind") == "artifact"]
    assert len(art) == 1, out[1]["parts"]
    assert art[0]["label"] == "voice"
    assert art[0]["path"].startswith("voice/")
    assert art[0]["mime"].startswith("audio/"), art[0]
    assert art[0]["size"] > 0
    # the user turn must stay untouched
    assert all(p.get("kind") == "text" for p in out[0]["parts"])


def test_turn_without_audio_gets_no_player(tmp_path, monkeypatch) -> None:
    """Voice toggled off / TTS unavailable → the turn simply has no player."""
    _seed_voice(tmp_path, monkeypatch, "s1", "etwas anderes")
    turns = [_mk_turn("assistant", "diese Antwort wurde nie gesprochen")]
    out = cr.attach_voice_artifacts("_default", "s1", turns)
    assert all(p.get("kind") == "text" for p in out[0]["parts"])


def test_attach_is_idempotent(tmp_path, monkeypatch) -> None:
    """History is read repeatedly; a second pass must not stack a second player."""
    text = "Wiederholung"
    _seed_voice(tmp_path, monkeypatch, "s1", text)
    turns = [_mk_turn("assistant", text)]
    cr.attach_voice_artifacts("_default", "s1", turns)
    cr.attach_voice_artifacts("_default", "s1", turns)
    art = [p for p in turns[0]["parts"] if p.get("kind") == "artifact"]
    assert len(art) == 1, turns[0]["parts"]


def test_missing_archive_dir_never_breaks_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / "nope")
    turns = [_mk_turn("assistant", "x")]
    out = cr.attach_voice_artifacts("_default", "s1", turns)  # must not raise
    assert out[0]["parts"][0]["kind"] == "text"


def test_matches_whichever_extension_landed(tmp_path, monkeypatch) -> None:
    """say.py emits OGG/MP3/WAV depending on the provider — the lookup must not
    assume one container."""
    text = "Provider-abhängig"
    _seed_voice(tmp_path, monkeypatch, "s1", text, ext=".mp3")
    turns = [_mk_turn("assistant", text)]
    out = cr.attach_voice_artifacts("_default", "s1", turns)
    art = [p for p in out[0]["parts"] if p.get("kind") == "artifact"]
    assert len(art) == 1 and art[0]["name"].endswith(".mp3"), art


# ── _persist_turn_voice: extension follows the SNIFFED mime ──────────────────

def test_persist_uses_sniffed_extension_not_a_hardcoded_one(tmp_path, monkeypatch) -> None:
    """say.py does not transcode: a file named .opus may hold MP3 bytes. The
    archive must label the container correctly or the browser can't decode it."""
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / s)
    (tmp_path / "s1").mkdir(parents=True)
    name = voice_routes._persist_turn_voice("_default", "s1", "Text", b"ID3\x00mp3", "audio/mpeg")
    assert name and name.endswith(".mp3"), name
    assert (tmp_path / "s1" / "voice" / name).read_bytes() == b"ID3\x00mp3"


def test_persist_leaves_no_tmp_file(tmp_path, monkeypatch) -> None:
    """The write is atomic — a half-written file must never be servable."""
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / s)
    (tmp_path / "s1").mkdir(parents=True)
    voice_routes._persist_turn_voice("_default", "s1", "Text", b"OggS\x00", "audio/ogg")
    assert not list((tmp_path / "s1" / "voice").glob("*.tmp"))


def test_persist_skips_unknown_session(tmp_path, monkeypatch) -> None:
    """Never create stray dirs for an unknown/expired sid."""
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / "missing")
    assert voice_routes._persist_turn_voice("_default", "s1", "T", b"OggS", "audio/ogg") is None
    assert not (tmp_path / "missing").exists()


def test_persist_never_raises_on_failure(tmp_path, monkeypatch) -> None:
    """Archiving must never break playback."""
    def _boom(tid, s):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(cr, "_workdir", _boom)
    assert voice_routes._persist_turn_voice("_default", "s1", "T", b"OggS", "audio/ogg") is None
