"""Fresh-system voice summary, end to end with REAL `claude -p` and REAL speech
synthesis on an install that has NO OpenAI key (edge-tts → piper chain).

Per-task recap (owed by a Voice-on turn), the automatic session recap, its
history, the on-demand session recap, plain TTS, and the voice status routes —
all through the installed product's HTTP/WebSocket API, asserting on the audio
bytes and on the recap TEXT. Gated by CORVIN_FRESH_E2E=1 — see fresh_console.py.
"""
from __future__ import annotations

import pytest
from fresh_console import (
    answer_text,
    audio_kind,
    final_result,
    login,
    new_session,
    product_python,
    skip_unless_fresh,
    wait_for,
    ws_turn,
)

pytestmark = skip_unless_fresh


@pytest.fixture(scope="module")
def console():
    c, cookie = login()
    yield c, cookie
    c.close()


def _audio(c, audio_url: str):
    r = c.get(audio_url.replace("/v1/console", "", 1))
    assert r.status_code == 200, (audio_url, r.status_code)
    kind = audio_kind(r.content)
    assert kind in ("mp3", "ogg", "wav"), kind
    assert len(r.content) > 10_000, len(r.content)
    assert r.headers.get("content-type", "").startswith("audio/"), r.headers.get("content-type")
    return kind, len(r.content)


def test_voice_status_on_a_fresh_install(console):
    c, _ = console
    st = c.get("/voice/status").json()
    assert st["tts"]["edge"]["ready"] is True, st["tts"]
    assert st["tts"]["openai"]["key_configured"] is False, st["tts"]
    assert st["offline_voice"]["state"] in ("ready", "queued", "downloading", "online_only"), st["offline_voice"]
    health = c.get("/chat/voice/health").json()
    assert health["status"] in ("healthy", "degraded"), health


def test_voice_on_turn_owes_and_delivers_task_and_session_recaps(console):
    c, cookie = console
    sid = new_session(c, "fresh voice e2e")
    events = ws_turn(cookie, sid, "Erkläre in drei kurzen Sätzen, warum der Himmel blau ist.", voice_on=True)
    res = final_result(events)
    assert res and res["outcome"] == "ok", res
    assert "blau" in res["text"].lower(), res["text"]

    # the text lands first, the audio after the synthesis — wait for the audio
    task = wait_for(lambda: [x for x in (c.get(f"/voice/task-summaries?sid={sid}").json() or {}).get("summaries", [])
                             if x.get("audio_url")], timeout=300)
    assert task, "no per-task voice summary with audio within 300 s"
    item = task[0]
    assert item["sid"] == sid and item["task_id"] and item["audio_url"], item
    assert item["lang"] == "de", item          # the chat's language, not a hardcoded default
    assert len(item["text"]) > 60 and ("himmel" in item["text"].lower() or "blau" in item["text"].lower()), item["text"]
    _audio(c, item["audio_url"])

    sess = wait_for(lambda: [x for x in c.get("/voice/summaries").json().get("summaries", []) if x["sid"] == sid],
                    timeout=240)
    assert sess, "no automatic session recap within 240 s"
    assert sess[0]["lang"] == "de" and "blau" in sess[0]["text"].lower(), sess[0]
    _audio(c, sess[0]["audio_url"])
    hist = c.get(f"/voice/summaries/{sid}/history").json()
    assert hist["count"] >= 1 and hist["history"][0]["audio_url"], hist
    # what the Voice Summaries page reads (web-next/src/lib/api/voice-summaries.ts)
    for key in ("sid", "title", "created_at", "lang", "text", "audio_url"):
        assert key in sess[0], key
    for key in ("sid", "task_id", "title", "created_at", "completed_at", "lang", "text", "audio_url"):
        assert key in item, key


def test_english_chat_gets_an_english_recap(console):
    """Until 2026-10-21 every automatic recap was German regardless of the chat."""
    c, cookie = console
    sid = new_session(c, "fresh voice en e2e")
    events = ws_turn(cookie, sid, "In two short sentences: why do leaves turn red in autumn?", voice_on=True)
    assert final_result(events)["outcome"] == "ok"
    task = wait_for(lambda: [x for x in (c.get(f"/voice/task-summaries?sid={sid}").json() or {}).get("summaries", [])
                             if x.get("audio_url")], timeout=300)
    assert task, "no per-task voice summary with audio within 300 s"
    assert task[0]["lang"] == "en", task[0]
    _audio(c, task[0]["audio_url"])
    low = task[0]["text"].lower()
    assert ("leaf" in low or "leaves" in low or "autumn" in low) and " der " not in f" {low} ", task[0]["text"]


def test_on_demand_session_recap_and_tts(console):
    c, cookie = console
    sid = new_session(c, "fresh voice tts e2e")
    ws_turn(cookie, sid, "Nenne in einem Satz die Hauptstadt von Frankreich.")
    r = c.post("/voice/session-summary", json={"sid": sid, "lang": "de"}, timeout=400)
    assert r.status_code == 200, (r.status_code, r.headers)
    assert audio_kind(r.content) in ("mp3", "ogg", "wav") and len(r.content) > 10_000
    assert r.headers.get("x-corvin-lang") == "de"
    for lang, text in (("de", "Hallo! Das ist ein kurzer Test der Sprachausgabe."),
                       ("en", "Hello! This is a short test of the speech output.")):
        r = c.post("/voice/tts", json={"text": text, "lang": lang, "sid": sid}, timeout=400)
        assert r.status_code == 200, (lang, r.status_code, r.headers.get("x-corvin-voice-reason"))
        assert audio_kind(r.content) in ("mp3", "ogg", "wav") and len(r.content) > 5_000
        assert r.headers.get("x-corvin-tts-provider"), r.headers
        assert "openai" not in r.headers.get("x-corvin-tts-provider", ""), "no key → never OpenAI"
    assert answer_text(ws_turn(cookie, sid, "Antworte nur mit PONG.")).upper().find("PONG") >= 0


def test_summarize_without_a_language_follows_the_text(console):
    """`POST /voice/summarize` defaulted to German: an English text came back as a German recap."""
    c, _ = console
    text = ("Leaves turn red in autumn because chlorophyll breaks down and anthocyanins, produced from "
            "sugars trapped in the leaf, become visible. Cold nights and sunny days make the colours stronger.")
    r = c.post("/voice/summarize", json={"text": text}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    low = r.json()["summary"].lower()
    assert ("leaf" in low or "leaves" in low or "autumn" in low or "chlorophyll" in low) and " der " not in f" {low} ", low


def test_transcribe_a_wav_from_the_installed_piper(console):
    """A real WAV from the installed offline voice through the real STT route (local Whisper)."""
    import subprocess
    from pathlib import Path
    c, _ = console
    say = Path(__file__).resolve().parents[3] / "corvin_operator" / "voice" / "scripts" / "say.py"
    wav = Path("/tmp") / "fresh_piper_sample.wav"
    # say.py needs the PRODUCT's interpreter (piper-tts lives there), not the test runner's
    py = product_python()
    proc = subprocess.run([py, str(say), str(wav), "The train to Hamburg leaves at nine from platform three.",
                           "en", "", "piper"], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not wav.exists():
        pytest.skip(f"piper not usable here: {proc.stderr[-200:]}")
    r = c.post("/voice/transcribe", files={"audio": ("sample.wav", wav.read_bytes(), "audio/wav")},
               data={"lang": "en"}, timeout=600)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    body = r.json()
    assert body["ok"] and body["provider"], body
    low = body["text"].lower()
    assert "hamburg" in low and ("nine" in low or "9" in low), body["text"]

