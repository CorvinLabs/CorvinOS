"""Group-chat command dispatcher E2E (ADR-2235 follow-up).

Through the real console transport (local-login + CSRF): a `/` line typed in a
group composer is parsed server-side and run through the same federation
primitives as in the 1:1 peer thread — it is never stored as a group message
and never fanned out to the group's peers as text. Several peers need an
explicit ``--in``; unknown lines are refused fail-closed.
"""
from __future__ import annotations

import pytest

from tests.federation.test_federation_cross_peer_e2e import (  # noqa: F401 — fixture
    _RecordingEngine,
    federation_pair,
)
from tests.federation.test_federation_routes_e2e import console  # noqa: F401 — fixture

BASE = "/v1/console/chat"
FED_BASE = "/v1/console/federation"
LOCAL = "opus-private"
PEER = "haiku-cheap"
_GATE = {"active": True}


@pytest.fixture
def group(console, monkeypatch):
    import core.federation.conversation as conv

    c, h, pair = console
    monkeypatch.setattr(conv, "_engine_factory", lambda: _RecordingEngine())
    assert c.post(f"{FED_BASE}/peers/peer-B/refresh", headers=h).status_code == 200
    # The live friendship gate reads the console's own endpoint dir, which the
    # federation_pair fixture does not populate — stand in for it, switchable.
    import core.console.corvin_console.routes.chat_groups as cg
    from fastapi import HTTPException

    _GATE["active"] = True

    def _gate(endpoint_id: str) -> None:
        if not _GATE["active"]:
            raise HTTPException(status_code=404, detail="no active friendship for this peer")

    monkeypatch.setattr(cg, "require_friendship_active", _gate)
    g = c.post(f"{BASE}/groups", json={"title": "g"}, headers=h).json()
    gid = g["group_id"]
    me = g["participants"][0]["participant_id"]
    r = c.post(f"{BASE}/groups/{gid}/participants", headers=h, json={
        "participant_id": "peer-b-part", "kind": "a2a_peer", "display_name": "B",
        "peer_endpoint_id": "peer-B"})
    assert r.status_code in (200, 201), r.text
    yield c, h, gid, me


def _cmd(c, h, gid, me, line):
    return c.post(f"{BASE}/groups/{gid}/command",
                  json={"line": line, "sender_participant_id": me}, headers=h)


def _stored(c, h, gid):
    return c.get(f"{BASE}/groups/{gid}/messages", headers=h).json()


def test_table_is_served(group):
    c, h, *_ = group
    r = c.get(f"{BASE}/group-commands", headers=h)
    assert r.status_code == 200
    assert {"/ask @mine", "/ask @peer", "/talk", "/stop", "/agents"} <= {e["cmd"] for e in r.json()["commands"]}


def test_ask_mine_runs_locally_and_is_not_a_group_message(group):
    c, h, gid, me = group
    before = len(_RecordingEngine.spawns)
    r = _cmd(c, h, gid, me, f"/ask @mine/{LOCAL} who are you")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["executed"] is True and body["kind"] == "ask_mine" and body["participant"] == "peer-b-part"
    assert len(_RecordingEngine.spawns) == before + 1
    assert _stored(c, h, gid) == []  # never stored, never fanned out


def test_ask_peer_goes_through_delegation(group):
    c, h, gid, me = group
    body = _cmd(c, h, gid, me, f"/ask @peer/{PEER} summarize").json()
    assert body["executed"] is True and body["kind"] == "ask_peer" and body["ok"] is True


def test_agents_covers_every_peer(group):
    c, h, gid, me = group
    body = _cmd(c, h, gid, me, "/agents").json()
    assert body["executed"] is True and [p["participant"] for p in body["peers"]] == ["peer-b-part"]


def test_unknown_and_console_commands_are_refused_not_sent(group):
    c, h, gid, me = group
    for line in ("/delegate x", "/new", "/nope"):
        body = _cmd(c, h, gid, me, line).json()
        assert body["executed"] is False and "not sent" in body["reason"]
    assert _stored(c, h, gid) == []


def test_unknown_selector_is_refused(group):
    c, h, gid, me = group
    body = _cmd(c, h, gid, me, "/ask --in nobody @mine hi").json()
    assert body["executed"] is False and "nobody" in body["reason"]


def test_two_peers_need_a_selector(group):
    c, h, gid, me = group
    r = c.post(f"{BASE}/groups/{gid}/participants", headers=h, json={
        "participant_id": "peer-c-part", "kind": "a2a_peer", "display_name": "C",
        "peer_endpoint_id": "peer-C"})
    assert r.status_code in (200, 201), r.text
    body = _cmd(c, h, gid, me, "/ask @mine hi").json()
    assert body["executed"] is False and "--in" in body["reason"]


def test_revoked_friendship_refuses_without_running(group):
    c, h, gid, me = group
    before = len(_RecordingEngine.spawns)
    _GATE["active"] = False
    body = _cmd(c, h, gid, me, f"/ask @mine/{LOCAL} hi").json()
    assert body["executed"] is False and "friendship" in body["reason"]
    assert len(_RecordingEngine.spawns) == before


def test_non_participant_sender_is_403(group):
    c, h, gid, _ = group
    assert _cmd(c, h, gid, "stranger", "/agents").status_code == 403


def test_csrf_required(group):
    c, h, gid, me = group
    r = c.post(f"{BASE}/groups/{gid}/command", json={"line": "/agents", "sender_participant_id": me})
    assert r.status_code == 403
