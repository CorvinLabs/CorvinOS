"""Peer-chat command dispatcher E2E (ADR-2235 Phase 2).

Through the real console transport (local-login + CSRF,
``/v1/console/peer-thread/*``): proves the bug the operator reported —
`/ask @mine …` typed in a peer chat reaching the PEER's agent instead of the
local one — cannot happen any more, because every `/` line is parsed and
dispatched HERE, server-side, before anything is ever sent. `/ask @mine`
runs a real local turn through ``a2a_worker.spawn_a2a_worker`` (never over
the wire); `/ask @peer` crosses a REAL ``RemoteTriggerSender`` → REAL
``a2a_http_server`` of a second instance, exactly like ADR-2232 delegation,
and produces a trace hop; an unrecognised line (including a console-session
command like `/delegate`, which is NOT peer-chat grammar) is refused with
"unknown command — not sent" and spawns nothing anywhere; CSRF missing
is a plain 403, same as every other mutating route.
"""
from __future__ import annotations

import pytest

from tests.federation.test_federation_cross_peer_e2e import (  # noqa: F401 — fixture
    _RecordingEngine,
    federation_pair,
)
from tests.federation.test_federation_routes_e2e import console  # noqa: F401 — fixture

BASE = "/v1/console/peer-thread"
FED_BASE = "/v1/console/federation"
LOCAL = "opus-private"   # registered by federation_pair, NOT federable
PEER = "haiku-cheap"     # registered by federation_pair, federable


@pytest.fixture
def thread(console, monkeypatch):
    import core.federation.conversation as conv

    c, h, pair = console
    monkeypatch.setattr(conv, "_engine_factory", lambda: _RecordingEngine())
    assert c.post(f"{FED_BASE}/peers/peer-B/refresh", headers=h).status_code == 200
    yield c, h, pair


def _cmd(c, h, line, **kw):
    return c.post(f"{BASE}/peer-B/command", json={"line": line}, headers=h, **kw)


def test_commands_table_is_served_not_hardcoded_client_side(thread):
    c, h, _ = thread
    r = c.get(f"{BASE}/commands", headers=h)
    assert r.status_code == 200, r.text
    cmds = {entry["cmd"] for entry in r.json()["commands"]}
    assert {"/ask @mine", "/ask @peer", "/talk", "/stop", "/agents"} <= cmds


def test_ask_mine_runs_locally_and_never_reaches_the_peer(thread):
    c, h, _ = thread
    before = len(_RecordingEngine.spawns)

    r = _cmd(c, h, f"/ask @mine/{LOCAL} welcher agent bist du")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True and body["kind"] == "ask_mine"
    assert body["status"] == "completed"
    assert body["agent_id"] == LOCAL
    assert "default-model" not in body["text"]  # the stub names its real model
    assert body["text"].startswith("done by claude-opus-5")

    # Exactly one spawn happened, and it ran with LOCAL's own model — never
    # sent to the peer as A2A text (that was the reported bug).
    assert len(_RecordingEngine.spawns) == before + 1
    assert _RecordingEngine.spawns[-1]["model"].startswith("claude-opus-5")

    listed = c.get(f"{FED_BASE}/conversations", headers=h).json()["conversations"]
    mine = [x for x in listed if x["conversation_id"] == body["conversation_id"]]
    assert len(mine) == 1 and mine[0]["ask"] is True and mine[0]["status"] == "completed"


def test_ask_peer_crosses_the_wire_and_produces_a_trace_hop(thread):
    c, h, pair = thread
    r = _cmd(c, h, f"/ask @peer/{PEER} summarize this for me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True and body["kind"] == "ask_peer"
    assert body["ok"] is True
    assert body["address"] == f"agent://{pair['iid_b']}/{PEER}"

    tree = c.get(f"{FED_BASE}/tasks/{body['task_id']}/trace", headers=h).json()
    assert tree["tree"]["task_id"] == body["task_id"]
    assert tree["tree"]["anchors"]["peer_chain_tail"]

    # /ask @peer IS operator-typed text — no thread_ref override, the
    # default peer-thread role derivation (direction=out,kind=task→operator)
    # already renders it correctly as "You".
    feed = c.get("/v1/console/a2a/feed", params={"peer_id": "peer-B"}, headers=h).json()
    sent = next(m for m in feed["messages"] if m["task_id"] == body["task_id"])
    assert sent["thread_ref"] is None


def test_talk_starts_and_stop_ends_a_conversation_in_this_thread(thread):
    c, h, _ = thread
    # --turns 2 so a PEER turn actually runs (turn 1 is local, turn 2 is
    # peer) — a 1-turn conversation never touches the wire at all.
    r = _cmd(c, h, f"/talk @mine/{LOCAL} @peer/{PEER} --turns 2 Agree on one word")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True and body["kind"] == "talk" and body["status"] == "running"

    import time
    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        status = c.get(f"{FED_BASE}/conversations/{body['conversation_id']}", headers=h).json()
        if status["status"] != "running":
            break
        time.sleep(0.05)
    assert status is not None and status["status"] == "completed"
    assert [m["speaker"] for m in status["messages"]] == ["operator", "local", "peer"]

    # ADR-2235 point 2: the moderator's turn-prompt sent to the peer is
    # feed-recorded (direction=out, kind=task) and must carry thread_ref so
    # the peer-thread UI never renders it as "You" — it's framing/history
    # text our own agent's turn generated, not something the operator typed.
    peer_turn_task_id = status["messages"][-1]["task_id"]
    feed = c.get("/v1/console/a2a/feed", params={"peer_id": "peer-B"}, headers=h).json()
    outbound_tasks = [m for m in feed["messages"] if m["direction"] == "out" and m["kind"] == "task"]
    conv_task = next(m for m in outbound_tasks if m["task_id"] == peer_turn_task_id)
    assert conv_task["thread_ref"] == {
        "kind": "conversation", "id": body["conversation_id"],
        "author_role": "local_agent", "agent_id": LOCAL,
    }

    # /stop on an already-finished conversation in this thread: no-op, not an error.
    r = _cmd(c, h, "/stop")
    assert r.status_code == 200 and r.json() == {"executed": True, "kind": "stop", "stopped": False}


def test_unrecognised_lines_are_refused_and_spawn_nothing(thread):
    c, h, _ = thread
    before = len(_RecordingEngine.spawns)

    r = _cmd(c, h, "/frobnicate")
    assert r.status_code == 200, r.text
    assert r.json() == {"executed": False, "reason": "unknown command — not sent"}

    # A console-session command is NOT peer-chat grammar (ADR-2235 point 3) —
    # it must be refused exactly like any other unknown line, never forwarded.
    r = _cmd(c, h, "/delegate do something")
    assert r.status_code == 200, r.text
    assert r.json() == {"executed": False, "reason": "unknown command — not sent"}

    assert len(_RecordingEngine.spawns) == before  # nothing ran anywhere


def test_csrf_missing_is_refused_before_dispatch(thread):
    c, h, _ = thread
    r = c.post(f"{BASE}/peer-B/command", json={"line": f"/ask @mine/{LOCAL} hi"})
    assert r.status_code == 403, r.text


def test_agents_lists_local_and_this_peers_federable_agents(thread):
    c, h, _ = thread
    r = _cmd(c, h, "/agents")
    assert r.status_code == 200, r.text
    body = r.json()
    assert LOCAL in body["local"]
    assert PEER in body["peer"]


def _expire_peer_catalog():
    """Age every stored catalog snapshot far past CATALOG_TTL_S."""
    import json

    from core.federation.peer_catalog import PeerCatalog

    ledger = PeerCatalog("_default")._ledger_path
    recs = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    for rec in recs:
        rec["fetched_at"] = 0.0
    ledger.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_ask_peer_refreshes_an_expired_catalog_instead_of_failing(thread):
    c, h, pair = thread
    _expire_peer_catalog()
    assert c.get(f"{FED_BASE}/peer-agents", headers=h).json()["agents"] == []  # really expired

    r = _cmd(c, h, f"/ask @peer/{PEER} summarize this for me")  # needs the catalog to resolve
    assert r.status_code == 200, r.text
    assert r.json().get("executed") is True, r.json()
    assert r.json()["address"] == f"agent://{pair['iid_b']}/{PEER}"
    assert c.get(f"{FED_BASE}/peer-agents", headers=h).json()["agents"]  # catalog is fresh again


def test_ask_peer_reports_the_refresh_failure_reason(thread, monkeypatch):
    from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

    c, h, _ = thread
    _expire_peer_catalog()

    def _boom(self, endpoint_id, **kw):
        raise PeerCatalogError("peer_unreachable")

    monkeypatch.setattr(PeerCatalog, "refresh", _boom)
    r = _cmd(c, h, "/ask @peer hello")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is False and "peer_unreachable" in body["reason"]
