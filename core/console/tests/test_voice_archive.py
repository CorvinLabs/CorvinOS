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


# ── ADR-0194 Phase 3: full read-aloud segmentation ───────────────────────────
# The automatic voice is a SUMMARY by construction. Phase 3 speaks the WHOLE
# answer, split into provider-sized segments. COVERAGE is the contract: a
# splitter that silently drops a tail would reintroduce the very defect this
# phase removes ("a big part is never actually read aloud").

def _words(s: str) -> list[str]:
    return s.split()


def test_segments_cover_every_word_in_order() -> None:
    """The contract. Nothing dropped, nothing reordered, nothing duplicated."""
    text = ("Erster Satz zum Thema. Zweiter Satz mit mehr Inhalt! Dritter Satz?\n\n"
            "Ein neuer Absatz folgt hier. " + "Fuellsatz mit ein paar Woertern. " * 200)
    segs = cr.split_for_speech(text, max_chars=300)
    assert len(segs) > 1, "long text must actually split"
    assert _words(" ".join(segs)) == _words(text), "segmentation lost or reordered words"


def test_segments_respect_the_cap() -> None:
    text = "Ein Satz mit mehreren Woertern. " * 150
    segs = cr.split_for_speech(text, max_chars=200)
    assert segs and all(len(s) <= 200 for s in segs), [len(s) for s in segs]


def test_short_text_is_one_segment() -> None:
    assert cr.split_for_speech("Kurz und knapp.", max_chars=1800) == ["Kurz und knapp."]


def test_empty_text_yields_no_segments() -> None:
    assert cr.split_for_speech("") == []
    assert cr.split_for_speech("   \n  ") == []


def test_prefers_sentence_boundaries() -> None:
    """Segments should end on a sentence, not mid-thought, when they can."""
    text = "Satz eins ist hier. Satz zwei ist da. Satz drei ist dort. Satz vier endet."
    segs = cr.split_for_speech(text, max_chars=40)
    assert len(segs) > 1
    assert all(s.rstrip().endswith((".", "!", "?")) for s in segs), segs


def test_never_cuts_mid_word() -> None:
    text = "Donaudampfschifffahrtsgesellschaftskapitaen faehrt heute. " * 40
    segs = cr.split_for_speech(text, max_chars=120)
    for w in _words(" ".join(segs)):
        assert w in text, f"segmentation invented/cut a token: {w!r}"


def test_oversized_single_token_is_emitted_whole_not_cut() -> None:
    """A URL/hash longer than the cap must survive intact — a mid-token cut is
    unspeakable and would corrupt the very input the cap protects."""
    url = "https://example.com/" + "x" * 300
    segs = cr.split_for_speech(f"Siehe hier. {url} Ende.", max_chars=100)
    assert any(url in s for s in segs), "oversized token was cut apart"
    assert _words(" ".join(segs)) == _words(f"Siehe hier. {url} Ende.")


# ── Phase 3: full read-aloud segments are archived + replayable ───────────────

def _seed_segments(tmp_path, monkeypatch, sid: str, text: str, n: int) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda tid, s: tmp_path / s)
    vdir = tmp_path / sid / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (vdir / f"{cr.voice_key(text)}-f{i:02d}.ogg").write_bytes(b"OggS" + b"\x00" * 32)


def test_full_segments_rehydrate_in_playback_order(tmp_path, monkeypatch) -> None:
    """Zero-padded indices are what make a lexical sort the right order — a
    12-segment read-aloud must not play -f10 before -f09."""
    text = "Lange Antwort."
    _seed_segments(tmp_path, monkeypatch, "s1", text, 12)
    turns = [_mk_turn("assistant", text)]
    out = cr.attach_voice_artifacts("_default", "s1", turns)
    arts = [p for p in out[0]["parts"] if p.get("kind") == "artifact"]
    assert len(arts) == 12, arts
    assert [a["label"] for a in arts][:3] == ["voice 1/12", "voice 2/12", "voice 3/12"]
    names = [a["name"] for a in arts]
    assert names == sorted(names), "segments must rehydrate in playback order"
    assert names[9].endswith("-f09.ogg") and names[10].endswith("-f10.ogg")


def test_summary_and_segments_are_separate_archives(tmp_path, monkeypatch) -> None:
    """`<key>.ext` (summary) must not be swept up by the `<key>-f*` glob, nor vice
    versa — they are two renderings of the same turn."""
    text = "Beides vorhanden."
    _seed_voice(tmp_path, monkeypatch, "s1", text)          # summary
    _seed_segments(tmp_path, monkeypatch, "s1", text, 2)    # full read-aloud
    assert cr.find_turn_voice("_default", "s1", text).name.endswith(f"{cr.voice_key(text)}.ogg")
    segs = cr.find_turn_voice_segments("_default", "s1", text)
    assert len(segs) == 2 and all("-f" in s.name for s in segs)
    turns = [_mk_turn("assistant", text)]
    arts = [p for p in cr.attach_voice_artifacts("_default", "s1", turns)[0]["parts"]
            if p.get("kind") == "artifact"]
    assert [a["label"] for a in arts] == ["voice", "voice 1/2", "voice 2/2"]


def test_segments_attach_is_idempotent(tmp_path, monkeypatch) -> None:
    text = "Nicht doppelt."
    _seed_segments(tmp_path, monkeypatch, "s1", text, 3)
    turns = [_mk_turn("assistant", text)]
    cr.attach_voice_artifacts("_default", "s1", turns)
    cr.attach_voice_artifacts("_default", "s1", turns)
    arts = [p for p in turns[0]["parts"] if p.get("kind") == "artifact"]
    assert len(arts) == 3, [a["label"] for a in arts]


def test_no_segments_means_no_extra_players(tmp_path, monkeypatch) -> None:
    """A turn nobody asked to hear in full keeps just its summary player."""
    text = "Nur Zusammenfassung."
    _seed_voice(tmp_path, monkeypatch, "s1", text)
    turns = [_mk_turn("assistant", text)]
    arts = [p for p in cr.attach_voice_artifacts("_default", "s1", turns)[0]["parts"]
            if p.get("kind") == "artifact"]
    assert [a["label"] for a in arts] == ["voice"]


# ── --audience / --output-language reach summarize.py (bridge parity) ────────
#
# Both were promised but never passed. The docstring on _summarize_for_speech
# claimed the "learnings/metaphor annex included, per the user's audience
# settings" from the day it was written, yet the argv it built carried neither
# --audience (so the console voice NEVER spoke the LERN-ZUGABE that every
# messenger bridge speaks) nor --output-language (so a Chinese/Japanese/French
# answer fell back to --lang de and was summarised, and therefore spoken, in
# GERMAN — defeating ADR-0194 Phase 2's "the spoken language follows the text").

def _capture_argv(monkeypatch, out: list) -> None:
    """Run _summarize_for_speech against a stubbed subprocess, capturing argv."""
    class _Proc:
        returncode = 0
        stdout = "a summary"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        out.append(cmd)
        return _Proc()

    monkeypatch.setattr(voice_routes.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        voice_routes, "_VOICE_SCRIPTS", Path(voice_routes.__file__).parent)
    monkeypatch.setattr(Path, "exists", lambda self: True)


def test_audience_block_is_passed_to_the_summarizer(monkeypatch) -> None:
    argv: list = []
    _capture_argv(monkeypatch, argv)
    monkeypatch.setattr(voice_routes, "_tts_audience_block", lambda lang: "LERN-ZUGABE: ja")
    voice_routes._summarize_for_speech("some long answer", "de")
    assert "--audience" in argv[0]
    assert argv[0][argv[0].index("--audience") + 1] == "LERN-ZUGABE: ja"


def test_no_audience_configured_omits_the_flag(monkeypatch) -> None:
    argv: list = []
    _capture_argv(monkeypatch, argv)
    monkeypatch.setattr(voice_routes, "_tts_audience_block", lambda lang: "")
    voice_routes._summarize_for_speech("some long answer", "de")
    assert "--audience" not in argv[0]


def test_third_language_gets_an_explicit_output_language(monkeypatch) -> None:
    argv: list = []
    _capture_argv(monkeypatch, argv)
    monkeypatch.setattr(voice_routes, "_tts_audience_block", lambda lang: "")
    voice_routes._summarize_for_speech("some long answer", "zh-Hans")
    # The pivot stays de|en, so without this the summary comes back German.
    assert argv[0][argv[0].index("--output-language") + 1] == "zh-Hans"


def test_pivot_languages_need_no_output_language_override(monkeypatch) -> None:
    argv: list = []
    _capture_argv(monkeypatch, argv)
    monkeypatch.setattr(voice_routes, "_tts_audience_block", lambda lang: "")
    voice_routes._summarize_for_speech("some long answer", "en")
    assert "--output-language" not in argv[0]
    assert argv[0][argv[0].index("--lang") + 1] == "en"


def test_a_broken_profile_never_breaks_tts(monkeypatch) -> None:
    """The audience block is a nice-to-have; TTS is not."""
    class _Boom:
        @staticmethod
        def for_tts_audience(lang):
            raise RuntimeError("profile.json is corrupt")

    monkeypatch.setattr(voice_routes, "_PROFILE_OK", True)
    monkeypatch.setattr(voice_routes, "_profile_module", _Boom)
    assert voice_routes._tts_audience_block("de") == ""


# ── the writer/reader must meet on TOOL-USING turns too ──────────────────────
#
# The writer (/voice/tts) hashes the text of the last `result` event — the CLI's
# FINAL assistant message. The reader hashed the persisted turn, which
# concatenates EVERY assistant text block of the turn. On a tool-using turn the
# two diverge, so the archived audio was orphaned and no player ever appeared.
# In an agentic console tool-using turns are the common case. Reproduced against
# the live archive before the fix: every single-block turn had a player, the one
# browser-tool turn had none.

def test_tool_using_turn_still_finds_its_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    spoken = "Die Datei enthaelt X."          # what /voice/tts was handed + archived
    (vd / f"{cr.voice_key(spoken)}.ogg").write_bytes(b"OggS-audio")

    # The turn as persisted: narration + tool card + final answer.
    turn = {
        "role": "assistant",
        "voice_key": cr.voice_key(spoken),
        "parts": [
            {"kind": "text", "text": "Ich schaue nach.Die Datei enthaelt X."},
            {"kind": "tool", "name": "Read"},
        ],
    }
    out = cr.attach_voice_artifacts("_default", "sid1", [turn])
    labels = [p.get("label") for p in out[0]["parts"] if p.get("kind") == "artifact"]
    assert labels == ["voice"], "tool-using turn must rehydrate its player"


def test_legacy_turn_without_the_hint_still_works(tmp_path, monkeypatch) -> None:
    """Turns persisted before the hint existed must keep the old behaviour."""
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    text = "Ein einzelner Textblock."
    (vd / f"{cr.voice_key(text)}.ogg").write_bytes(b"OggS-audio")
    turn = {"role": "assistant", "parts": [{"kind": "text", "text": text}]}  # no voice_key
    out = cr.attach_voice_artifacts("_default", "sid1", [turn])
    assert [p.get("label") for p in out[0]["parts"] if p.get("kind") == "artifact"] == ["voice"]


def test_a_stale_tmp_file_is_never_served_as_audio(tmp_path, monkeypatch) -> None:
    """A crash/ENOSPC mid-write leaves `<key>.<ext>.tmp`, which matches the glob."""
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    text = "Halbfertig."
    (vd / f"{cr.voice_key(text)}.ogg.tmp").write_bytes(b"half-written")
    assert cr.find_turn_voice("_default", "sid1", text) is None


def test_a_stale_segment_tmp_does_not_inflate_the_labels(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    text = "Langer Text."
    k = cr.voice_key(text)
    (vd / f"{k}-f00.ogg").write_bytes(b"OggS-1")
    (vd / f"{k}-f01.ogg").write_bytes(b"OggS-2")
    (vd / f"{k}-f02.ogg.tmp").write_bytes(b"torn")   # never finished
    segs = cr.find_turn_voice_segments("_default", "sid1", text)
    assert [p.name for p in segs] == [f"{k}-f00.ogg", f"{k}-f01.ogg"]


# ── the segmenter must make progress on space-free scripts ───────────────────
#
# Both boundary levels used to assume ASCII/spaces: the sentence regex only knew
# [.!?] (CJK uses 。！？, and puts no space after them) and the fallback packer
# splits on whitespace, of which CJK prose has none. A Chinese answer therefore
# came back as ONE segment far over the cap — reproduced at 2800 chars WITH full
# stops, 5000 without. Past the provider's 4096 limit say.py exits non-zero,
# /voice/segment returns 204, and playFull reads 204 as end-of-playlist: no
# read-aloud at all, silently, for exactly the users who cannot skim the text.

def test_chinese_without_any_punctuation_still_respects_the_cap() -> None:
    segs = cr.split_for_speech("汉" * 5000)
    assert len(segs) > 1
    assert max(len(s) for s in segs) <= cr._VOICE_SEGMENT_MAX_CHARS


def test_cjk_full_stops_are_sentence_boundaries() -> None:
    """。！？ split with no trailing whitespace — CJK doesn't use one."""
    segs = cr.split_for_speech("这是一个句子。" * 400)
    assert len(segs) > 1
    assert max(len(s) for s in segs) <= cr._VOICE_SEGMENT_MAX_CHARS


def test_japanese_and_korean_also_segment() -> None:
    for src in ("これはテストです。" * 300, "이것은문장입니다。" * 300):
        segs = cr.split_for_speech(src)
        assert len(segs) > 1
        assert max(len(s) for s in segs) <= cr._VOICE_SEGMENT_MAX_CHARS


def test_cjk_coverage_is_exact() -> None:
    """The coverage contract holds for sliced runs too: every char, once, in order."""
    src = "这是一个句子。" * 400
    assert "".join(cr.split_for_speech(src)).replace(" ", "") == src


def test_an_oversized_latin_url_is_still_emitted_whole() -> None:
    """The space-free slicing must NOT start cutting links.

    Slicing CJK is safe because the script has no word spaces — there is no word
    to cut in half. A URL is one real token: an unspeakable fragment is worse
    than an oversized segment, so it keeps the old emit-whole behaviour.
    """
    url = "https://example.com/" + "a" * 3000
    segs = cr.split_for_speech(f"Sieh hier. {url} Ende.")
    assert any(url in s for s in segs)


def test_space_free_detector_does_not_claim_latin() -> None:
    assert cr._is_space_free_script("汉字文本")
    assert cr._is_space_free_script("これはテスト")
    assert not cr._is_space_free_script("https://example.com/a/b")
    assert not cr._is_space_free_script("Donaudampfschifffahrtsgesellschaft")
    assert not cr._is_space_free_script("")


# ── retention: the archive must not grow without bound ───────────────────────
#
# There was no cap of any kind. _MAX_VOICE_SEGMENTS bounds segments PER TURN and
# _MAX_SESSIONS_PER_TENANT bounds session COUNT, but one long-lived chat kept
# speech audio for every turn forever. Beyond storage that is a GDPR Art. 5(1)(e)
# storage-limitation problem. Eviction is oldest-first because the files are
# keyed by a hash of the text with no back-reference, so "delete the audio for
# turn X" is not expressible — mtime is.

def test_prune_evicts_oldest_until_under_the_cap(tmp_path, monkeypatch) -> None:
    import os as _os
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    for i in range(5):
        f = vd / f"{'%016x' % i}.ogg"
        f.write_bytes(b"x" * 100)
        _os.utime(f, (1000 + i, 1000 + i))      # oldest = i0
    removed = cr.prune_voice_archive("_default", "sid1", max_bytes=250)
    survivors = sorted(p.name for p in vd.iterdir())
    assert removed == 3
    assert survivors == ["%016x.ogg" % 3, "%016x.ogg" % 4]   # newest kept


def test_prune_is_a_noop_under_the_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    (vd / "aaaaaaaaaaaaaaaa.ogg").write_bytes(b"x" * 100)
    assert cr.prune_voice_archive("_default", "sid1", max_bytes=10_000) == 0
    assert len(list(vd.iterdir())) == 1


def test_prune_never_raises_on_a_missing_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    assert cr.prune_voice_archive("_default", "sid1", max_bytes=1) == 0


def test_prune_disabled_by_a_zero_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    (vd / "aaaaaaaaaaaaaaaa.ogg").write_bytes(b"x" * 100)
    assert cr.prune_voice_archive("_default", "sid1", max_bytes=0) == 0
    assert len(list(vd.iterdir())) == 1


def test_an_evicted_turn_just_loses_its_player(tmp_path, monkeypatch) -> None:
    """Eviction must degrade to 'no player', never to a broken one."""
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    text = "Eine alte Antwort."
    (vd / f"{cr.voice_key(text)}.ogg").write_bytes(b"x" * 100)
    cr.prune_voice_archive("_default", "sid1", max_bytes=1)
    assert cr.find_turn_voice("_default", "sid1", text) is None
    turn = {"role": "assistant", "parts": [{"kind": "text", "text": text}]}
    out = cr.attach_voice_artifacts("_default", "sid1", [turn])
    assert [p for p in out[0]["parts"] if p.get("kind") == "artifact"] == []


def test_prune_never_evicts_the_file_just_written(tmp_path, monkeypatch) -> None:
    """A mis-set tiny cap must not eat the current turn's audio.

    Found by attacking the fix: with max_bytes below a single file's size, the
    oldest-first loop happily deleted the file _persist_turn_voice had just
    written — so every turn synthesised its audio and immediately dropped it,
    burning the TTS spend and never rendering a player.
    """
    import os as _os
    monkeypatch.setattr(cr, "_workdir", lambda t, s: tmp_path)
    vd = tmp_path / "voice"
    vd.mkdir()
    for i in range(3):
        f = vd / f"{'%016x' % i}.ogg"
        f.write_bytes(b"x" * 100)
        _os.utime(f, (1000 + i, 1000 + i))
    fresh = vd / "ffffffffffffffff.ogg"
    fresh.write_bytes(b"x" * 100)

    cr.prune_voice_archive("_default", "sid1", max_bytes=50, keep=fresh.name)
    assert fresh.exists(), "the just-written audio must survive any cap"
    assert sorted(p.name for p in vd.iterdir()) == [fresh.name]
