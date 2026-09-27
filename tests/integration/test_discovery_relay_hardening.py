"""Adversarial regression tests for the Discovery Relay (ADR-2061/2062).

Pins four defects found in the 2026-09-27 review:

1. The register HMAC covered only ``{"instance_id": ...}``, so one observed
   registration signature authorised re-registering the same instance with an
   attacker-chosen ``endpoint`` (catalog poisoning / traffic hijack).
2. The heartbeat HMAC signed the IDENTICAL message, so any heartbeat header
   observed on the wire was also a valid registration signature.
3. Audit records were written to a hand-composed
   ``Path.home()/.corvin/tenants/<tid>/global/audit.jsonl`` — neither the
   canonical ``tenant_audit_chain()`` file nor ``CORVIN_HOME``-aware (a test
   run wrote into the operator's live install).
4. ``discovery.*`` events had no ``_EVENT_ALLOWLIST`` entry, so the
   vocabulary floor silently dropped ``org_id`` / ``instance_id`` / counts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from corvin_operator.discovery_relay.relay import create_relay_app
from corvin_operator.discovery_relay.security import (
    compute_hmac,
    encrypt_payload,
    heartbeat_signing_payload,
    register_signing_payload,
)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _register_body(org_key: bytes, instance_id: str, endpoint: str) -> dict:
    tier_enc, nonce = encrypt_payload({"tier": "pro", "kid": "kid_v1"}, org_key)
    return {
        "instance_id": instance_id,
        "endpoint": endpoint,
        "tier_enc": tier_enc,
        "tier_enc_nonce": nonce,
        "kid": "kid_v1",
        "latency_ms": 7,
    }


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    corvin_home = tmp_path / "corvin_home"
    home.mkdir()
    corvin_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
    return home, corvin_home


@pytest.mark.asyncio
async def test_register_signature_does_not_authorise_a_different_endpoint(isolated_home):
    app, relay = create_relay_app()
    key = os.urandom(32)
    relay.set_org_key("org", key)
    body = _register_body(key, "inst1", "https://good.example:443")
    sig = compute_hmac(register_signing_payload("org", body), key)
    async with _client(app) as c:
        ok = await c.post("/discovery/org/register", json=body,
                          headers={"Authorization": f"Bearer {sig}"})
        assert ok.status_code == 200
        forged = dict(body, endpoint="https://attacker.example:443")
        r = await c.post("/discovery/org/register", json=forged,
                         headers={"Authorization": f"Bearer {sig}"})
        assert r.status_code == 401
        cat = (await c.get("/discovery/org/catalog")).json()
    assert [i["endpoint"] for i in cat["instances"]] == ["https://good.example:443"]


@pytest.mark.asyncio
async def test_heartbeat_signature_is_not_a_registration_signature(isolated_home):
    app, relay = create_relay_app()
    key = os.urandom(32)
    relay.set_org_key("org", key)
    hb_sig = compute_hmac(heartbeat_signing_payload("org", "inst1"), key)
    body = _register_body(key, "inst1", "https://attacker.example:443")
    async with _client(app) as c:
        r = await c.post("/discovery/org/register", json=body,
                         headers={"Authorization": f"Bearer {hb_sig}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_audit_lands_on_canonical_tenant_chain_with_fields(isolated_home):
    home, corvin_home = isolated_home
    app, relay = create_relay_app()
    key = os.urandom(32)
    relay.set_org_key("org", key)
    body = _register_body(key, "inst1", "https://good.example:443")
    sig = compute_hmac(register_signing_payload("org", body), key)
    async with _client(app) as c:
        r = await c.post("/discovery/org/register", json=body,
                         headers={"Authorization": f"Bearer {sig}"})
        assert r.status_code == 200
        hb = compute_hmac(heartbeat_signing_payload("org", "inst1"), key)
        r = await c.post("/discovery/org/heartbeat",
                         json={"instance_id": "inst1", "latency_ms": 9},
                         headers={"Authorization": f"Bearer {hb}"})
        assert r.status_code == 200

    # Nothing under $HOME/.corvin — the relay honours CORVIN_HOME.
    assert not (home / ".corvin").exists()
    chain = corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert chain.is_file(), "audit must land on tenant_audit_chain()"
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    by_type = {r["event_type"]: r for r in recs}
    reg = by_type["discovery.instance_registered"]["details"]
    assert reg["org_id"] == "org" and reg["instance_id"] == "inst1"
    assert "_dropped_fields" not in reg
    hbd = by_type["discovery.instance_heartbeat"]["details"]
    assert hbd["instance_id"] == "inst1" and hbd["latency_ms"] == 9
    assert "_dropped_fields" not in hbd
