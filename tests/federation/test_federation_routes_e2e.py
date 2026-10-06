"""Console E2E for cross-peer federation (ADR-2232).

Real console transport end to end: ``/auth/local-login`` + CSRF, the
``/v1/console/federation/*`` REST routes, and the chat WebSocket for the
``/federation`` slash command. ``/delegate`` and ``/peers/{id}/refresh`` go
on through the REAL ``RemoteTriggerSender`` to a REAL ``a2a_http_server``
of a second instance (``federation_pair``), so one request crosses two HTTP
boundaries and two signature checks. Only B's engine is a fake.
"""
from __future__ import annotations

import os

import pytest

from tests.federation.test_federation_cross_peer_e2e import (  # noqa: F401 — fixture
    _RecordingEngine,
    federation_pair,
)

BASE = "/v1/console/federation"


@pytest.fixture
def console(federation_pair, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import core.federation.delegation as delegation_mod
    import core.federation.peer_catalog as catalog_mod
    from core.console.corvin_console.app import router as console_router

    # Point the console's default A2A sender at instance A's paired endpoint.
    monkeypatch.setattr(catalog_mod, "default_sender", lambda: federation_pair["sender"])
    monkeypatch.setattr(delegation_mod, "default_sender", lambda: federation_pair["sender"])
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", "/bin/false")  # a slash command must never spawn

    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    with TestClient(app, client=("127.0.0.1", 51236)) as c:
        r = c.get("/v1/console/auth/local-login", follow_redirects=False)
        assert r.status_code in (200, 302, 307), r.text
        csrf = c.get("/v1/console/auth/whoami").json()["csrf_token"]
        yield c, {"x-csrf-token": csrf}, federation_pair


def test_refresh_then_list_peers_and_peer_agents(console):
    c, h, pair = console
    r = c.post(f"{BASE}/peers/peer-B/refresh", headers=h)
    assert r.status_code == 200, r.text
    assert sorted(a["agent_id"] for a in r.json()["agents"]) == ["haiku-cheap", "opus-code"]

    peers = c.get(f"{BASE}/peers").json()["peers"]
    assert peers[0]["endpoint_id"] == "peer-B" and peers[0]["peer_instance_id"] == pair["iid_b"]
    code = c.get(f"{BASE}/peer-agents", params={"capability": "code_execution"}).json()["agents"]
    assert [a["address"] for a in code] == [f"agent://{pair['iid_b']}/opus-code"]


def test_select_ranks_across_installations(console):
    c, h, _ = console
    c.post(f"{BASE}/peers/peer-B/refresh", headers=h)
    r = c.post(f"{BASE}/select", json={"capability": "analysis", "include_local": False}, headers=h)
    assert r.status_code == 200, r.text
    assert [x["agent_id"] for x in r.json()["candidates"]] == ["haiku-cheap", "opus-code"]
    assert all(x["scope"] == "peer" for x in r.json()["candidates"])


def test_delegate_and_trace_through_both_instances(console):
    c, h, pair = console
    c.post(f"{BASE}/peers/peer-B/refresh", headers=h)
    r = c.post(f"{BASE}/delegate", json={"instruction": "Check this.", "capability": "analysis"},
               headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["agent_id"] == "haiku-cheap"
    assert body["address"] == f"agent://{pair['iid_b']}/haiku-cheap"
    assert _RecordingEngine.spawns and _RecordingEngine.spawns[-1]["model"].startswith("claude-haiku-4-5")

    t = c.get(f"{BASE}/tasks/{body['task_id']}/trace")
    assert t.status_code == 200, t.text
    node = t.json()["tree"]
    assert node["task_id"] == body["task_id"] and node["status"] == "ok"
    assert node["anchors"]["peer_chain_tail"]


def test_delegate_refused_by_peer_is_reported_not_raised(console):
    c, h, _ = console
    r = c.post(f"{BASE}/delegate",
               json={"instruction": "x", "endpoint_id": "peer-B", "agent_id": "ghost"}, headers=h)
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["error"] == "federation_unknown_agent"


def test_bad_inputs_are_rejected(console):
    c, h, _ = console
    assert c.post(f"{BASE}/peers/..%2Fetc/refresh", headers=h).status_code in (400, 404)
    assert c.post(f"{BASE}/delegate", json={"instruction": "x", "endpoint_id": "a b"},
                  headers=h).status_code == 400
    assert c.post(f"{BASE}/delegate", json={"instruction": "x"}, headers=h).status_code == 400
    assert c.post(f"{BASE}/select", json={"capability": "telepathy"}, headers=h).status_code == 400
    assert c.get(f"{BASE}/tasks/never-delegated/trace").status_code == 404


def test_mutations_require_csrf(console):
    c, _, _ = console
    assert c.post(f"{BASE}/delegate", json={"instruction": "x", "capability": "analysis"}).status_code == 403


def test_federation_slash_command_over_the_chat_websocket(console):
    c, h, pair = console
    c.post(f"{BASE}/peers/peer-B/refresh", headers=h)
    sid = c.post("/v1/console/chat/sessions", json={"title": "fed"}, headers=h).json()["session"]["sid"]
    spawns_before = len(_RecordingEngine.spawns)
    with c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user", "text": "/federation analysis"})
        text = ""
        for _ in range(200):
            msg = ws.receive_json()
            if msg.get("type") == "delta":
                text += msg.get("text") or ""
            if msg.get("type") == "done":
                break
    assert "**Local agents**" in text and "`haiku-cheap`" in text
    assert f"agent://{pair['iid_b']}/haiku-cheap" in text
    assert "codex-local" not in text, "capability filter applies to local agents too"
    assert len(_RecordingEngine.spawns) == spawns_before
