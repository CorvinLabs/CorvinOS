"""gap/chat — release-readiness chat gaps, proven through the real interfaces.

Real ``uvicorn`` console in a sandbox (``harness``), HTTP and WebSocket only; the
audit records are written by the product's own writer (``bridges/shared/audit.py``)
in a child process of the sandbox, never composed by hand.

Gaps covered (LIVE-TEST-PLAN §6, Chat rows):
  G5  OS-turn / engine-span / execution-log views read the writer's chain (S-11b)
  G6  UI-generated A2A invite codes are listed and revocable
  G2  the slash palette lists only commands that act
  G3  /create workflow|tool|skill answer honestly and spend no model turn
  G4  /erase needs a confirmation code before it erases

Red on origin/main (925406772), green on gap/chat.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402

REPO = harness.REPO_ROOT
SHARED = str(REPO / "corvin_operator" / "bridges" / "shared")
PALETTE = REPO / "core/console/corvin_console/web-next/src/components/chat/SlashCommandPalette.tsx"

WRITE_TURN = r"""
import sys
shared, chat_key, turn_id = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, shared)
import audit, engine_span
audit.audit_event("os_turn.started", channel="console", chat_key=chat_key, persona="assistant",
                  details={"turn_id": turn_id, "model": "claude-haiku-4-5"})
engine_span.emit_start(audit.audit_event, span_id="span-" + turn_id, role="os", engine_id="claude_code",
                       model_id="claude-haiku-4-5", run_id=turn_id, turn_id=turn_id,
                       channel="console", chat_key=chat_key)
audit.audit_event("os_turn.completed", channel="console", chat_key=chat_key, persona="assistant",
                  details={"turn_id": turn_id, "duration_ms": 12, "tools_called": 0, "exit_code": 0,
                           "model": "claude-haiku-4-5"})
engine_span.emit_end(audit.audit_event, span_id="span-" + turn_id, role="os", engine_id="claude_code",
                     model_id="claude-haiku-4-5", run_id=turn_id, turn_id=turn_id, status="ok",
                     duration_ms=12, tokens_used=3, channel="console", chat_key=chat_key)
print(audit.audit_path())
"""


@pytest.fixture(scope="module")
def sess(session_for):
    return session_for("_default")


def _new_chat(server, sess) -> dict:
    r = harness.fetch(server, sess, "/chat/sessions", method="POST", json_body={"title": "gap chat"})
    assert r.status_code in (200, 201), r.text[:300]
    body = r.json()
    return body.get("session", body)


# ── G5: the three audit views read the writer's chain ───────────────────────

def test_G5_views_read_the_chain_the_writer_wrote(server, sandbox, sess):
    chat = _new_chat(server, sess)
    sid, chat_key = chat["sid"], chat["chat_key"]
    out = sandbox.run_child(WRITE_TURN, SHARED, chat_key, "turn-gap5").strip().splitlines()[-1]
    canonical = sandbox.tenant_home("_default") / "global" / "forge" / "audit.jsonl"
    assert Path(out) == canonical, f"writer wrote {out}, expected the tenant chain {canonical}"
    assert "os_turn.completed" in canonical.read_text(encoding="utf-8")

    turns = harness.fetch(server, sess, f"/chat/sessions/{sid}/os-turns")
    assert turns.status_code == 200, turns.text[:300]
    assert [t["turn_id"] for t in turns.json()["turns"]] == ["turn-gap5"], turns.text[:400]

    spans = harness.fetch(server, sess, f"/chat/sessions/{sid}/engine-spans")
    assert spans.status_code == 200
    assert [s["span_id"] for s in spans.json()["spans"]] == ["span-turn-gap5"], spans.text[:400]

    log = harness.fetch(server, sess, f"/chat/sessions/{sid}/execution-log")
    assert log.status_code == 200
    kinds = [e["event_type"] for e in log.json()["entries"]]
    assert "os_turn.started" in kinds and "os_turn.completed" in kinds, kinds


# ── G6: UI-generated invite codes reach the registry ────────────────────────

def test_G6_generated_invite_is_listed_and_revocable(server, sandbox, sess):
    gen = harness.fetch(server, sess, "/remote-trigger/pair/generate", method="POST", json_body={
        "label": "gap6 peer", "url": "http://127.0.0.1:9/v1/a2a/receive",
        "console_url": "http://127.0.0.1:9", "peer_origin_id": "gap6-peer", "ttl_minutes": 30})
    assert gen.status_code == 200, gen.text[:300]
    accept_id = gen.json()["accept_id"]
    pending = Path(sandbox.env["REMOTE_PENDING_DIR"]) / f"{accept_id}.json"
    assert pending.is_file()

    listed = harness.fetch(server, sess, "/remote-trigger/pair/invites")
    assert listed.status_code == 200
    rows = {i["ikey"]: i for i in listed.json()["invites"]}
    assert accept_id in rows, f"Pending invites does not list the generated code: {list(rows)}"
    assert rows[accept_id]["status"] == "pending" and rows[accept_id]["lbl"] == "gap6 peer"

    rev = harness.fetch(server, sess, f"/remote-trigger/pair/invites/{accept_id}", method="DELETE")
    assert rev.status_code == 200, rev.text[:300]
    after = {i["ikey"]: i for i in harness.fetch(server, sess, "/remote-trigger/pair/invites").json()["invites"]}
    assert after[accept_id]["status"] == "revoked"
    assert not pending.exists(), "a revoked invite must not stay redeemable"
    # the issuer-side accept endpoint (server-to-server) now refuses it
    with server.client() as c:
        r = c.post("/remote-trigger/pair/accept", json={
            "accept_id": accept_id, "ts": time.time(), "nonce": "n" * 16,
            "redeemer_instance_id": "x", "redeemer_url": "http://127.0.0.1:9", "redeemer_label": "x",
            "origin_id_for_us": "gap6-redeemer", "signature": "0" * 64})
    assert r.status_code in (404, 410), r.text[:200]


# ── WebSocket helper for the slash-command gaps ─────────────────────────────

def _ws_turn(server, sess, sid: str, text: str, *, timeout: float = 30.0) -> dict:
    from websockets.sync.client import connect

    url = server.base.replace("http://", "ws://") + f"{server.prefix}/chat/sessions/{sid}/stream"
    texts: list[str] = []
    events: list[dict] = []
    with connect(url, additional_headers={"Cookie": f"{harness.COOKIE}={sess.sid}"}, origin=server.base, open_timeout=15) as ws:
        ready = json.loads(ws.recv(timeout=15))
        assert ready.get("type") == "ready", ready
        ws.send(json.dumps({"type": "user", "text": text}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = json.loads(ws.recv(timeout=max(1.0, deadline - time.monotonic())))
            events.append(ev)
            if ev.get("type") == "delta":
                texts.append(ev.get("text", ""))
            if ev.get("type") in ("done", "error"):
                break
    return {"text": "".join(texts), "events": events}


def _palette_commands() -> list[str]:
    src = PALETTE.read_text(encoding="utf-8")
    block = src[src.index("export const SLASH_COMMANDS"):]
    block = block[:block.index("] as const;")]
    return re.findall(r'cmd:\s*"(/[^"]+)"', block)


# Answered by the composer / the turn pipeline (model-bound by design), not by the
# deterministic dispatcher — they are legitimate palette entries.
_NOT_DISPATCHER = {"/stop", "/new", "/btw", "/delegate", "/engine-auto", "/debug-engine",
                   "/use-engine tiered_delegation", "/use-engine acs", "/use-engine claude_code"}
_DEAD_REPLY = re.compile(r"Unknown command|messaging-bridge command|Use the \*\*New chat\*\* button|"
                         r"toggled in the|is shown in the \*\*|are listed in the \*\*|Active skills are listed", re.I)


def test_G2_every_palette_command_acts(server, sess):
    chat = _new_chat(server, sess)
    dead: dict[str, str] = {}
    cmds = _palette_commands()
    assert "/help" in cmds and "/audit" in cmds and "/federation" in cmds and "/resume" in cmds
    for cmd in cmds:
        if cmd in _NOT_DISPATCHER:
            continue
        line = {"/create task": '/create task name="gap2 palette task"',
                "/erase": "/erase user uid=gap2-nobody",
                "/resume": "/resume gap2-task",
                "/plugin-builder": "/plugin-builder status"}.get(cmd, cmd)
        reply = _ws_turn(server, sess, chat["sid"], line)["text"]
        if not reply.strip() or _DEAD_REPLY.search(reply):
            dead[cmd] = reply[:120]
    assert not dead, f"palette lists commands that do nothing: {dead}"


def test_G2_help_only_describes_acting_commands(server, sess):
    chat = _new_chat(server, sess)
    text = _ws_turn(server, sess, chat["sid"], "/help")["text"]
    for dead in ("/skills", "/memory", "/dialectic-on", "/clear", "/reset", "/go", "/propose", "/share"):
        assert f"`{dead}`" not in text, f"/help still advertises {dead}"
    for live in ("/audit", "/federation", "/resume", "/new"):
        assert live in text


def test_G2_audit_command_shows_real_events(server, sandbox, sess):
    chat = _new_chat(server, sess)
    sandbox.run_child(WRITE_TURN, SHARED, chat["chat_key"], "turn-gap2audit")
    text = _ws_turn(server, sess, chat["sid"], "/audit last 5")["text"]
    assert "os_turn.completed" in text or "engine.span.end" in text, text
    assert "not_implemented" not in text and "Audit tab" not in text


# ── G3: no invented ids, no model turn ──────────────────────────────────────

@pytest.mark.parametrize("target", ["workflow", "tool", "skill"])
def test_G3_create_unbuilt_entities_is_honest_and_spends_no_model_turn(server, sess, target):
    chat = _new_chat(server, sess)
    res = _ws_turn(server, sess, chat["sid"], f'/create {target} name="gap3"')
    text = res["text"]
    cards = [e for e in res["events"] if e.get("type") == "ccc_action"]
    assert cards, "the command must report its outcome as an action card"
    for card in cards:
        assert card["status"] == "not_available", card
        assert card.get("entity_id") in (None, ""), f"invented id: {card}"
        assert not re.search(r"(wf|tool|skill)_[0-9a-f]{8}", json.dumps(card))
    assert "not available" in text.lower() and "queued" not in text.lower()
    # no model turn: the reply is the command's own sentence, nothing from an engine
    assert not any(e.get("type") in ("tool_use", "thinking", "usage") for e in res["events"])
    assert len([e for e in res["events"] if e.get("type") == "delta"]) == 1


def test_G3_create_task_creates_a_real_task(server, sess):
    chat = _new_chat(server, sess)
    res = _ws_turn(server, sess, chat["sid"], '/create task name="gap3 real task"')
    cards = [e for e in res["events"] if e.get("type") == "ccc_action"]
    assert cards and cards[0]["status"] == "created" and cards[0]["entity_id"], res["events"]
    assert cards[0]["entity_id"] in res["text"]


# ── G4: /erase needs a confirmation code ────────────────────────────────────

def test_G4_erase_asks_for_confirmation_before_erasing(server, sandbox, sess):
    chat = _new_chat(server, sess)
    trail = sandbox.tenant_home("_default") / "global" / "erasure"
    before = sorted(p.name for p in trail.glob("*.json")) if trail.is_dir() else []

    first = _ws_turn(server, sess, chat["sid"], "/erase user uid=gap4-subject")
    card = [e for e in first["events"] if e.get("type") == "ccc_action"][0]
    assert card["status"] == "confirmation_required", card
    assert "Nothing has been erased" in first["text"] or "Nothing has" in first["text"]
    after_first = sorted(p.name for p in trail.glob("*.json")) if trail.is_dir() else []
    assert after_first == before, "the first /erase must not execute the erasure"
    code = re.search(r"confirm=([A-Za-z0-9_-]{8,})", first["text"]).group(1)

    wrong = _ws_turn(server, sess, chat["sid"], "/erase user uid=gap4-subject confirm=WRONGWRONG1")
    assert "wrong, already used or expired" in wrong["text"]
    # a wrong attempt burns the code
    burned = _ws_turn(server, sess, chat["sid"], f"/erase user uid=gap4-subject confirm={code}")
    assert "wrong, already used or expired" in burned["text"]

    again = _ws_turn(server, sess, chat["sid"], "/erase user uid=gap4-subject")
    code2 = re.search(r"confirm=([A-Za-z0-9_-]{8,})", again["text"]).group(1)
    done = _ws_turn(server, sess, chat["sid"], f"/erase user uid=gap4-subject confirm={code2}")
    card2 = [e for e in done["events"] if e.get("type") == "ccc_action"][0]
    assert card2["entity_type"] == "erasure_request" and card2["status"] in ("created", "error")
    assert "Erasure" in done["text"], done["text"]
    final = sorted(p.name for p in trail.glob("*.json")) if trail.is_dir() else []
    assert len(final) == len(before) + 1, "the confirmed /erase must leave exactly one erasure trail"
    # the same code never works twice
    replay = _ws_turn(server, sess, chat["sid"], f"/erase user uid=gap4-subject confirm={code2}")
    assert "wrong, already used or expired" in replay["text"]
