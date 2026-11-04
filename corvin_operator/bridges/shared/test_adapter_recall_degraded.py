"""PLAN-0969 WP-4: when the conversation memory cannot be written, the chat says so.

``conversation_recall.index_turn`` never raises; a failure comes back as
``{"ok": False, "reason": ...}``, and until 2026-11-04 the adapter ignored that value, so a
Windows install whose recall store failed simply stopped remembering, silently.

Driven through the real ``adapter.process_one`` (fake CLI, sandboxed inbox/outbox/home). The
memory directory is made unwritable for real (a FILE where the directory must be), so
``index_turn`` fails on the filesystem, not on a mock. Asserts:

* the turn completes and the delivered text carries the notice, once per chat;
* a transient lock (``db-locked``) and a healthy store produce no notice;
* the spoken voice note carries the notice even when the summariser drops it
  (the real ``_synthesize_voice_for_turn``, only the summariser and the TTS call are stubbed).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent.parent / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_DE_PROMPT = "Bitte merke dir, dass mein Hund Rex heißt und am liebsten Käse frisst."


@pytest.fixture
def sandbox(monkeypatch):
    base = Path(tempfile.mkdtemp(prefix="adapter-recall-degraded-"))
    for d in ("inbox", "outbox", "processed", "bridges"):
        (base / d).mkdir()
    env = {
        "ADAPTER_INBOX": str(base / "inbox"),
        "ADAPTER_OUTBOX": str(base / "outbox"),
        "ADAPTER_PROCESSED": str(base / "processed"),
        "ADAPTER_FAKE_CLAUDE": "1",
        "ADAPTER_FAKE_DELAY": "0",
        "ADAPTER_ROUTING_MODE": "off",
        "ADAPTER_BRIDGES_DIR": str(base / "bridges"),
        "ADAPTER_DISABLE_VOICE": "1",
        "VOICE_AUDIT_PATH": str(base / "voice_audit.jsonl"),
        "CORVIN_HOME": str(base / "home"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for mod in ("adapter", "conversation_recall"):
        sys.modules.pop(mod, None)
    adapter = importlib.import_module("adapter")
    return base, adapter


def _break_memory_dir(base: Path) -> None:
    mem = base / "home" / "tenants" / "_default" / "global" / "memory"
    mem.parent.mkdir(parents=True, exist_ok=True)
    if mem.is_dir():
        mem.rmdir()
    mem.write_text("not a directory", encoding="utf-8")


def _send(base: Path, adapter, msg_id: str, text: str = _DE_PROMPT, chat: str = "chat-77") -> str:
    env = {"id": msg_id, "channel": "discord", "from": "u-1", "chat_id": chat,
           "text": text, "ts": time.time()}
    f = base / "inbox" / f"{msg_id}.json"
    f.write_text(json.dumps(env), encoding="utf-8")
    adapter.process_one(f, settings={"whitelist": ["u-1"]})
    out = []
    for p in sorted((base / "outbox").glob(f"*{msg_id}*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")).get("text", ""))
    return "\n".join(out)


def test_failed_memory_write_is_announced_once_per_chat(sandbox):
    base, adapter = sandbox
    _break_memory_dir(base)
    first = _send(base, adapter, "m-deg-1")
    assert "[fake" in first, f"turn did not complete: {first!r}"
    assert "Hinweis" in first and "Gedächtnis" in first, first
    second = _send(base, adapter, "m-deg-2")
    assert "[fake" in second
    assert "Gedächtnis" not in second, "the notice must be said once per chat, not every turn"


def test_healthy_store_has_no_notice(sandbox):
    base, adapter = sandbox
    text = _send(base, adapter, "m-ok-1")
    assert "[fake" in text and "Gedächtnis" not in text and "memory" not in text.lower()


def test_transient_lock_is_not_announced(sandbox, monkeypatch):
    base, adapter = sandbox
    import conversation_recall as cr

    monkeypatch.setattr(cr, "index_turn", lambda **k: {"ok": False, "reason": "db-locked"})
    monkeypatch.setattr(adapter, "_recall_prov", None, raising=False)
    text = _send(base, adapter, "m-lock-1")
    assert "[fake" in text and "Gedächtnis" not in text


def test_notice_helper_is_english_for_english_and_carries_no_path(sandbox):
    _, adapter = sandbox
    n = adapter._recall_degraded_notice({"ok": False, "reason": "db-error"}, "discord", "c-en", "en")
    assert n.startswith("Note:") and "memory" in n and "db-error" in n
    assert adapter._recall_degraded_notice({"ok": False, "reason": "db-error"}, "discord", "c-en", "en") == ""
    assert adapter._recall_degraded_notice({"ok": True}, "discord", "c-x", "en") == ""
    assert adapter._recall_degraded_notice(None, "discord", "c-y", "en") == ""


def test_spoken_note_carries_the_notice_even_if_the_summary_drops_it(sandbox, monkeypatch):
    _, adapter = sandbox
    monkeypatch.delenv("ADAPTER_DISABLE_VOICE", raising=False)
    spoken: list[str] = []
    monkeypatch.setattr(adapter, "build_voice_summary", lambda answer, **k: "Kurze Zusammenfassung.")
    monkeypatch.setattr(adapter, "synthesize_voice_note",
                        lambda text, **k: (spoken.append(text), Path("/tmp/x.mp3"))[1])
    notice = "Hinweis: Dieses Gespräch konnte nicht im Gedächtnis gespeichert werden."
    path, expected = adapter._synthesize_voice_for_turn(
        "kurz", {"voice_summary_mode": "long_only", "voice_threshold_chars": 10_000},
        None, "", {}, spoken_notice=notice)
    assert expected and path is not None, "a notice must force the voice note even under long_only"
    assert spoken and spoken[0].endswith(notice), spoken
