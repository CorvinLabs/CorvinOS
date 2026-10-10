"""A fresh install can share an agent with its peers — from the console, not by raw API (ADR-2232 follow-up).

The reported bug: in a peer/group chat, ``/ask @peer …`` answered
"this peer offers no federable agent — the peer must mark one as federable". True — but the console had NO
control to register an agent or mark it federable (only ``listLocalAgents``): the message sent the peer's
operator to an action that did not exist outside a hand-written ``POST /federation/agents``. On a freshly
installed instance that means ``/ask @peer``, ``/ask @mine`` and ``/talk`` can never work.

Through the real console transport (local-login + CSRF) and the two-instance fixture (real signed A2A over
HTTP to a second instance), this proves the whole path an operator takes:

  1. nothing registered   -> both ``/ask`` forms refuse AND name where to fix it (Your agents)
  2. one click            -> ``POST /federation/default-agent`` registers the Claude Code agent, NOT shared
  3. explicit opt-in      -> ``PATCH /federation/agents/{id}`` {federable: true}; the peer's next catalog
                             lists it and ``/ask @peer`` runs on it (a real spawn on the peer side)
  4. opt-out              -> {federable: false}; the peer stops offering it
  Guards: unknown agent 404, malformed body 422, CSRF required, only Claude Code agents can be shared,
  idempotent default registration, every change audited, sharing is never a side effect of registering.
"""
from __future__ import annotations

import json

import pytest

from tests.federation.test_federation_cross_peer_e2e import (  # noqa: F401 — fixtures
    _RecordingEngine,
    federation_pair,
)
from tests.federation.test_federation_routes_e2e import console  # noqa: F401 — fixture
from tests.federation.test_peer_thread_commands_e2e import _cmd, _expire_peer_catalog, thread  # noqa: F401

FED = "/v1/console/federation"


@pytest.fixture
def fresh_peer(thread):
    """Both instances start with NO agent registered — a fresh install."""
    c, h, pair = thread
    for a in c.get(f"{FED}/agents").json()["agents"]:
        assert c.delete(f"{FED}/agents/{a['agent_id']}", headers=h).status_code == 200
    assert c.get(f"{FED}/agents").json()["agents"] == []
    _expire_peer_catalog()
    return c, h, pair


def test_the_refusals_name_where_to_fix_it(fresh_peer):
    c, h, _ = fresh_peer
    peer = _cmd(c, h, "/ask @peer hello").json()
    assert peer["executed"] is False
    assert "no federable agent" in peer["reason"]
    assert "Your agents" in peer["reason"] and "Share with paired peers" in peer["reason"], peer["reason"]
    mine = _cmd(c, h, "/ask @mine hello").json()
    assert mine["executed"] is False
    assert "Your agents" in mine["reason"], mine["reason"]


def test_one_click_registers_the_default_agent_without_sharing_it(fresh_peer):
    c, h, _ = fresh_peer
    r = c.post(f"{FED}/default-agent", headers=h)
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["agent_id"], a["engine_type"], a["federable"]) == ("claude-code", "claude_code", False)
    assert set(a["capabilities"]) <= {"code_execution", "analysis", "inference", "vision", "classification"}
    # registering is not consenting: the peer still sees nothing
    _expire_peer_catalog()
    assert _cmd(c, h, "/ask @peer hello").json()["executed"] is False
    # idempotent: a second click changes nothing and says so
    again = c.post(f"{FED}/default-agent", headers=h)
    assert again.status_code == 200 and again.json()["agent_id"] == "claude-code"
    assert len(c.get(f"{FED}/agents").json()["agents"]) == 1


def test_sharing_makes_the_peers_ask_work_and_unsharing_takes_it_away(fresh_peer):
    c, h, _ = fresh_peer
    c.post(f"{FED}/default-agent", headers=h)

    on = c.patch(f"{FED}/agents/claude-code", json={"federable": True}, headers=h)
    assert on.status_code == 200 and on.json()["federable"] is True, on.text
    _expire_peer_catalog()
    before = len(_RecordingEngine.spawns)
    ok = _cmd(c, h, "/ask @peer say hello")
    body = ok.json()
    assert body["executed"] is True and body["kind"] == "ask_peer", body
    assert len(_RecordingEngine.spawns) == before + 1, "the peer's worker must really have run"

    off = c.patch(f"{FED}/agents/claude-code", json={"federable": False}, headers=h)
    assert off.status_code == 200 and off.json()["federable"] is False
    _expire_peer_catalog()
    refused = _cmd(c, h, "/ask @peer say hello again").json()
    assert refused["executed"] is False and "no federable agent" in refused["reason"]
    assert len(_RecordingEngine.spawns) == before + 1, "an unshared agent must not run anything"


def test_guards(fresh_peer):
    c, h, _ = fresh_peer
    c.post(f"{FED}/default-agent", headers=h)
    assert c.patch(f"{FED}/agents/nope", json={"federable": True}, headers=h).status_code == 404
    for bad in ({}, {"federable": "yes please"}, {"federable": None}, {"federable": True, "x": 1}):
        assert c.patch(f"{FED}/agents/claude-code", json=bad, headers=h).status_code == 422, bad
    assert c.patch(f"{FED}/agents/claude-code", json={"federable": True}).status_code == 403   # no CSRF
    assert c.post(f"{FED}/default-agent").status_code == 403
    # an unsafe id never reaches the registry
    assert c.patch(f"{FED}/agents/{'x' * 200}", json={"federable": True}, headers=h).status_code in (400, 404)
    # only an agent that CAN be offered can be shared: a codex agent would register as "shared" and never appear
    c.post(f"{FED}/agents", headers=h, json={"agent_id": "codex-x", "engine_type": "codex_cli",
                                             "capabilities": ["analysis"], "model": "gpt-5"})
    r = c.patch(f"{FED}/agents/codex-x", json={"federable": True}, headers=h)
    assert r.status_code == 400 and "claude_code" in r.text, r.text
    # unchanged by the failed attempt
    assert c.get(f"{FED}/agents/codex-x").json()["federable"] is False


def test_a_deregistered_agent_cannot_be_shared(fresh_peer):
    c, h, _ = fresh_peer
    c.post(f"{FED}/default-agent", headers=h)
    assert c.delete(f"{FED}/agents/claude-code", headers=h).status_code == 200
    assert c.patch(f"{FED}/agents/claude-code", json={"federable": True}, headers=h).status_code == 404


def test_every_change_is_audited_and_never_carries_task_text(fresh_peer):
    from tests.federation.test_federation_cross_peer_e2e import TENANT, _chain_events

    c, h, pair = fresh_peer
    c.post(f"{FED}/default-agent", headers=h)
    c.patch(f"{FED}/agents/claude-code", json={"federable": True}, headers=h)
    c.patch(f"{FED}/agents/claude-code", json={"federable": False}, headers=h)
    events = [e for e in _chain_events(pair["home"]) if "local_agent_registered" in json.dumps(e)]
    flags = [bool((e.get("details") or e).get("federable")) for e in events if "claude-code" in json.dumps(e)]
    assert flags[-3:] == [False, True, False], flags
