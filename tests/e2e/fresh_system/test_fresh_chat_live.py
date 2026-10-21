"""Fresh-system console chat, end to end with REAL model calls (2026-10-21).

Everything goes through the installed product's HTTP/WebSocket API: local-login,
capabilities, the engine probe, session creation, turns over the chat WebSocket,
the persisted turns, the audit chain, the session-ledger view, slash commands and
`/btw`. Gated by CORVIN_FRESH_E2E=1 — see fresh_console.py.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest
from fresh_console import (
    API,
    TENANT_HOME,
    answer_text,
    chain_rows,
    final_result,
    login,
    new_session,
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


def test_fresh_console_needs_no_operator_setup(console):
    """local-login → capabilities + settings + engine probe: Claude Code is
    installed, signed in and selected, with nothing the install did not do."""
    c, _ = console
    assert c.get("/capabilities").status_code == 200
    assert c.get("/settings").status_code == 200
    assert c.get("/chat-settings").status_code == 200
    eng = c.get("/settings/engine").json()
    assert eng["default_engine"] == "claude_code", eng
    det = c.get("/settings/engine/detect").json()
    claude = [r for r in det["results"] if r["engine_id"] == "claude_code"][0]
    assert claude["installed"] and claude["authenticated"], claude
    assert det["needs_bootstrap"] is False
    # The health route reports the SAME probe the chat gates a turn on — it
    # answered a constant "healthy" for a signed-out CLI until 2026-10-21.
    health = c.get("/settings/engine/health").json()
    assert health == {"healthy": True, "message": "Claude Code is ready"}, health


def test_a_real_turn_streams_persists_and_audits(console):
    c, cookie = console
    sid = new_session(c, "fresh chat e2e")
    t0 = time.time()
    events = ws_turn(cookie, sid, "Antworte nur mit dem Wort PONG.")
    types = [e["type"] for e in events]
    assert types[0] == "turn_started" and types[-1] == "done", types
    assert "language" in types and "engine" in types and "delta" in types, types
    res = final_result(events)
    assert res and res["outcome"] == "ok", res
    assert "PONG" in res["text"].upper(), res["text"]
    assert res["usage"]["output_tokens"] > 0, res["usage"]

    turns = c.get(f"/chat/sessions/{sid}/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"], turns
    ctx = turns[1]["execution_context"]
    assert ctx["engine_id"] == "claude_code" and ctx["model_name"], ctx
    assert turns[1]["cli_spawned"] is True
    # ADR-0952: a conversation turn routes to Haiku (automatic tier, no pin)
    assert "haiku" in ctx["model_name"], ctx["model_name"]

    rows = chain_rows(t0, "os_turn.completed")
    if rows is None:
        pytest.skip("audit chain not readable from here — API assertions above hold")
    rows = [r for r in rows if r["details"].get("chat_key") == f"web:{sid}"]
    assert rows, "no os_turn.completed for this chat in the tenant chain"
    d = rows[-1]["details"]
    assert d["model"] == ctx["model_name"] and d["exit_code"] == 0, d
    assert d["output_tokens"] > 0 and d["input_tokens"] + d["cache_read_input_tokens"] > 0, d
    started = [r for r in chain_rows(t0, "os_turn.started") or [] if r["details"].get("chat_key") == f"web:{sid}"]
    assert started and started[-1]["details"]["turn_id"] == d["turn_id"], "started/completed pair"

    # context survives into the next turn of the same chat
    events = ws_turn(cookie, sid, "Wiederhole bitte nur das eine Wort, mit dem du gerade geantwortet hast.")
    assert "PONG" in answer_text(events).upper(), answer_text(events)

    # the session ledger's view exists outside the record; it is regenerated at
    # every spawn FROM the turns before it, so after turn 2 it holds turn 1
    wd = c.get(f"/chat/sessions/{sid}/workdir-path").json().get("path")
    view = Path(wd) / ".corvin-history.md" if wd else None
    if view is not None and view.exists():
        text = view.read_text(encoding="utf-8")
        assert "Turn #1" in text and "Antworte nur mit dem Wort PONG." in text and "**Assistant:** PONG" in text


def test_the_bot_answers_in_the_users_language(console):
    """A fresh install seeds display_language from the system locale. That is a
    DEFAULT: German in → German out, English in → English out. (Until 2026-10-21
    the seed was treated as an operator pin and a German question got
    'the operator set English' back.)"""
    c, cookie = console
    sid = new_session(c, "fresh language e2e")
    en = answer_text(ws_turn(cookie, sid, "Answer in one short English sentence: what colour is the sky on a clear day?"))
    de = answer_text(ws_turn(cookie, sid, "Antworte in einem kurzen deutschen Satz: welche Farbe hat Gras?"))
    assert "blue" in en.lower(), en
    assert "grün" in de.lower() or "gruen" in de.lower(), de
    assert "green" not in de.lower().replace("grün", ""), de


def test_slash_commands_and_btw(console):
    c, cookie = console
    sid = new_session(c, "fresh slash e2e")
    help_text = answer_text(ws_turn(cookie, sid, "/help", timeout=30))
    assert "/help" in help_text and "Console commands" in help_text, help_text
    new_text = answer_text(ws_turn(cookie, sid, "/new", timeout=30))
    assert "New chat" in new_text, new_text
    # /btw with nothing running is simply the next message (same as the SPA);
    # it used to answer "not available in the web console".
    btw = answer_text(ws_turn(cookie, sid, "/btw Antworte ab jetzt immer nur mit dem Wort GRÜN."))
    assert "bridge" not in btw.lower(), btw
    follow = answer_text(ws_turn(cookie, sid, "Welches Wort sollst du jetzt sagen?"))
    assert "GRÜN" in follow.upper() or "GRUEN" in follow.upper(), follow
    # the REST route of the SPA refuses an empty note and reports no live turn
    r = c.post(f"/chat/sessions/{sid}/btw", json={"instruction": "noch kürzer"})
    assert r.status_code == 200 and r.json()["status"] == "no_active_stream", r.text


def test_cancel_mid_turn_keeps_the_chat_usable(console):
    c, cookie = console
    sid = new_session(c, "fresh cancel e2e")
    events = ws_turn(cookie, sid, "Zähle langsam von 1 bis 300, jede Zahl in einer eigenen Zeile, ohne Kommentar.",
                     cancel_after_first_delta=True, timeout=120)
    assert events[-1]["type"] == "done", events[-1]
    assert not [e for e in events if e["type"] == "error"], events
    turns = c.get(f"/chat/sessions/{sid}/turns").json()["turns"]
    assert turns and turns[0]["role"] == "user"
    res = final_result(ws_turn(cookie, sid, "Antworte nur mit PONG."))
    assert res and res["outcome"] == "ok" and "PONG" in res["text"].upper(), res


def test_restart_mid_turn_keeps_the_streamed_answer(console):
    """`systemctl --user restart corvin-webui` while a turn runs: the shutdown finalises
    the turn (audit pair, task interrupted) instead of killing it silently, and turn
    recovery re-runs a request that produced no output. Needs systemctl inside the system."""
    import shutil
    import subprocess
    if not shutil.which("systemctl"):
        pytest.skip("no systemctl here")
    c, cookie = console
    sid = new_session(c, "fresh restart e2e")
    t0 = time.time()

    async def run():
        import json as _json

        import websockets
        from fresh_console import WS_API
        async with websockets.connect(f"{WS_API}/chat/sessions/{sid}/stream",
                                      additional_headers={"Cookie": f"corvin_console_sid={cookie}"}) as ws:
            await ws.recv()
            await ws.send(_json.dumps({"type": "user", "voice_on": False, "text":
                "Schreibe einen zusammenhängenden deutschen Aufsatz von etwa 700 Wörtern über die "
                "Geschichte des Buchdrucks. Keine Werkzeuge benutzen, direkt schreiben."}))
            try:
                while True:
                    ev = _json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    if ev.get("type") == "engine":
                        await asyncio.sleep(6)   # the CLI is mid-answer now
                        subprocess.Popen(["systemctl", "--user", "restart", "corvin-webui"])
                    if ev.get("type") == "done":
                        return
            except Exception:  # noqa: BLE001 — the socket closes with the restart
                return
    asyncio.run(run())
    probe = httpx.Client(base_url=API, timeout=5)
    assert wait_for(lambda: _healthz(probe), timeout=120, every=2), "console did not come back"
    rows = chain_rows(t0, "os_turn.completed")
    if rows is not None:
        mine = [r for r in rows if r["details"].get("chat_key") == f"web:{sid}"]
        assert mine and mine[0]["details"]["exit_code"] == -1, "shutdown did not pair the cut-off turn"
    c2, _ = login()
    turns = wait_for(lambda: [t for t in c2.get(f"/chat/sessions/{sid}/turns").json()["turns"]
                              if t.get("recovery") or t.get("notice") == "interrupted_resuming"], timeout=120, every=5)
    assert turns, "turn recovery did not re-run the cut-off request"
    final = wait_for(lambda: [t for t in c2.get(f"/chat/sessions/{sid}/turns").json()["turns"]
                              if t["role"] == "assistant" and "Buchdruck" in "".join(p.get("text", "") for p in t["parts"])],
                     timeout=240, every=5)
    assert final, "no recovered answer about the request"


def _healthz(client) -> bool:
    try:
        return client.get("/healthz").status_code == 200
    except Exception:  # noqa: BLE001
        return False


def test_a_secret_in_the_prompt_never_reaches_the_chain(console):
    c, cookie = console
    sid = new_session(c, "fresh secret e2e")
    events = ws_turn(cookie, sid, "Mein AWS-Key ist AKIAIOSFODNN7EXAMPLE und mein Passwort ist hunter2. Antworte nur mit OK.")
    assert events[-1]["type"] == "done"
    chain = TENANT_HOME / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        pytest.skip("audit chain not readable from here")
    text = chain.read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in text and "hunter2" not in text
