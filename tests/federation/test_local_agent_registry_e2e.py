"""E2E tests for the Federation Local Agent Registry (CONCEPT-0097 Phase 1).

Every test drives the REAL HTTP route through the real console app (the
``client`` fixture from ``tests/fixtures_console.py``), not the
``LocalAgentRegistry`` class directly — satisfying the e2e-wiring-proof bar
(CLAUDE.md § E2E Wiring Proof): this proves the route is actually mounted
and reachable, not just that the underlying class works when imported.
"""
from __future__ import annotations

import json

import pytest

from core.paths import tenant_audit_chain

BASE = "/v1/console/federation/agents"
TENANT_ID = "_default"


def _read_chain_events(home) -> list[dict]:
    chain = tenant_audit_chain(TENANT_ID)
    if not chain.exists():
        return []
    events = []
    with chain.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


class TestRegisterLocalAgent:
    def test_register_success(self, tmp_corvin_home, client):
        resp = client.post(BASE, json={
            "agent_id": "claude-sync",
            "engine_type": "claude_code",
            "capabilities": ["code_execution", "analysis"],
            "model": "claude-opus-5",
            "max_concurrent": 2,
            "cost_per_task_usd": 0.05,
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_id"] == "claude-sync"
        assert body["engine_type"] == "claude_code"
        assert body["capabilities"] == ["analysis", "code_execution"]
        assert body["model"] == "claude-opus-5"
        assert body["max_concurrent"] == 2
        assert body["scope"] == "local"

    def test_register_writes_audit_event(self, tmp_corvin_home, client):
        resp = client.post(BASE, json={
            "agent_id": "audited-agent",
            "engine_type": "codex_cli",
            "capabilities": ["inference"],
            "model": "gpt-5",
        })
        assert resp.status_code == 201, resp.text

        events = _read_chain_events(tmp_corvin_home)
        matching = [e for e in events if e.get("event_type") == "federation.local_agent_registered"]
        assert len(matching) == 1, events
        assert matching[0]["details"]["agent_id"] == "audited-agent"
        assert matching[0]["details"]["tenant_id"] == TENANT_ID
        assert "hash" in matching[0]

    def test_register_empty_capabilities_rejected(self, tmp_corvin_home, client):
        resp = client.post(BASE, json={
            "agent_id": "bad-agent",
            "engine_type": "claude_code",
            "capabilities": [],
            "model": "claude-opus-5",
        })
        assert resp.status_code in (400, 422), resp.text

    def test_register_unknown_capability_rejected(self, tmp_corvin_home, client):
        resp = client.post(BASE, json={
            "agent_id": "bad-agent-2",
            "engine_type": "claude_code",
            "capabilities": ["telepathy"],
            "model": "claude-opus-5",
        })
        assert resp.status_code == 400, resp.text
        assert "telepathy" in resp.json()["detail"]

    def test_register_duplicate_agent_id_overwrites_latest(self, tmp_corvin_home, client):
        first = client.post(BASE, json={
            "agent_id": "dup-agent",
            "engine_type": "claude_code",
            "capabilities": ["analysis"],
            "model": "claude-haiku-4-5",
        })
        assert first.status_code == 201

        second = client.post(BASE, json={
            "agent_id": "dup-agent",
            "engine_type": "claude_code",
            "capabilities": ["vision"],
            "model": "claude-opus-5",
        })
        assert second.status_code == 201

        got = client.get(f"{BASE}/dup-agent")
        assert got.status_code == 200
        assert got.json()["capabilities"] == ["vision"]
        assert got.json()["model"] == "claude-opus-5"


class TestListAndGetLocalAgents:
    def test_list_empty(self, tmp_corvin_home, client):
        resp = client.get(BASE)
        assert resp.status_code == 200
        assert resp.json() == {"agents": []}

    def test_list_after_register(self, tmp_corvin_home, client):
        client.post(BASE, json={
            "agent_id": "lister-1",
            "engine_type": "claude_code",
            "capabilities": ["code_execution"],
            "model": "claude-opus-5",
        })
        client.post(BASE, json={
            "agent_id": "lister-2",
            "engine_type": "acs",
            "capabilities": ["inference"],
            "model": "claude-haiku-4-5",
        })
        resp = client.get(BASE)
        assert resp.status_code == 200
        ids = sorted(a["agent_id"] for a in resp.json()["agents"])
        assert ids == ["lister-1", "lister-2"]

    def test_list_filtered_by_capability(self, tmp_corvin_home, client):
        client.post(BASE, json={
            "agent_id": "cap-code",
            "engine_type": "claude_code",
            "capabilities": ["code_execution"],
            "model": "claude-opus-5",
        })
        client.post(BASE, json={
            "agent_id": "cap-vision",
            "engine_type": "claude_code",
            "capabilities": ["vision"],
            "model": "claude-opus-5",
        })
        resp = client.get(BASE, params={"capability": "vision"})
        assert resp.status_code == 200
        ids = [a["agent_id"] for a in resp.json()["agents"]]
        assert ids == ["cap-vision"]

    def test_get_single_agent_404_when_missing(self, tmp_corvin_home, client):
        resp = client.get(f"{BASE}/does-not-exist")
        assert resp.status_code == 404


class TestDeregisterLocalAgent:
    def test_deregister_removes_from_list(self, tmp_corvin_home, client):
        client.post(BASE, json={
            "agent_id": "to-remove",
            "engine_type": "claude_code",
            "capabilities": ["analysis"],
            "model": "claude-opus-5",
        })
        resp = client.delete(f"{BASE}/to-remove")
        assert resp.status_code == 200
        assert resp.json() == {"agent_id": "to-remove", "deregistered": True}

        listed = client.get(BASE)
        assert listed.json() == {"agents": []}

        single = client.get(f"{BASE}/to-remove")
        assert single.status_code == 404

    def test_deregister_unknown_agent_404(self, tmp_corvin_home, client):
        resp = client.delete(f"{BASE}/never-registered")
        assert resp.status_code == 404

    def test_deregister_writes_audit_event(self, tmp_corvin_home, client):
        client.post(BASE, json={
            "agent_id": "audit-deregister",
            "engine_type": "claude_code",
            "capabilities": ["analysis"],
            "model": "claude-opus-5",
        })
        client.delete(f"{BASE}/audit-deregister")

        events = _read_chain_events(tmp_corvin_home)
        matching = [e for e in events if e.get("event_type") == "federation.local_agent_deregistered"]
        assert len(matching) == 1, events
        assert matching[0]["details"]["agent_id"] == "audit-deregister"


class TestAuthRequired:
    def test_register_without_session_is_rejected(self, tmp_corvin_home, anon_client):
        resp = anon_client.post(BASE, json={
            "agent_id": "no-auth",
            "engine_type": "claude_code",
            "capabilities": ["analysis"],
            "model": "claude-opus-5",
        })
        assert resp.status_code == 401

    def test_list_without_session_is_rejected(self, tmp_corvin_home, anon_client):
        resp = anon_client.get(BASE)
        assert resp.status_code == 401
