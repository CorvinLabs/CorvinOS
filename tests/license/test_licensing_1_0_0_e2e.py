"""Licensing 1.0.0 — the capability-verification endpoint, over HTTP, in-process.

The previous version of this file (and of the gate3/gate4 files) sent ``requests``
to a live service on ``localhost:8765``. Tests never talk to a running install;
this drives the real console router in a scratch CORVIN_HOME instead.

Where the endpoint actually lives
---------------------------------
``routes/licensing_verify.py`` declares ``APIRouter(prefix="/v1/licensing")`` and
``corvin_console/app.py`` includes it in the console router — which both hosts
mount under ``/v1/console``. The reachable URL is therefore
``/v1/console/v1/licensing/verify``; ``/v1/licensing/verify`` (the URL the old
tests used) is not served by either host.

What it answers today (KNOWN BUG, pinned)
-----------------------------------------
``verify_capability`` passes ``tenant_id=tenant_id`` where no ``tenant_id`` is in
scope (it never takes the session: ``session = None  # Placeholder``). The
NameError is swallowed by its fail-closed ``except Exception`` and EVERY request
— baseline capabilities, member tier, anything — answers
``allowed: false, reason: "enforcement_unavailable", tier: "free"``. It fails
closed, so nothing is over-granted, but the endpoint carries no information.
The capability semantics it was meant to expose are tested directly against
``require_capability`` in test_licensing_1_0_0_gate3_red_green.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console, member_tier  # noqa: E402

import corvin_operator.license.capability_api as capability_api  # noqa: E402

VERIFY = "/v1/console/v1/licensing/verify"


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def test_endpoint_is_only_reachable_under_the_doubled_prefix(con):
    body = {"capability": "chat.turns"}
    assert con.client.post("/v1/licensing/verify", json=body, headers=con.h).status_code == 404
    assert con.client.post("/v1/console/licensing/verify", json=body, headers=con.h).status_code == 404
    assert con.client.post(VERIFY, json=body, headers=con.h).status_code == 200


def test_requires_a_session(con):
    resp = con.anonymous().post(VERIFY, json={"capability": "chat.turns"}, headers=con.h)
    assert resp.status_code == 401, resp.text


def test_requires_csrf(con):
    resp = con.client.post(VERIFY, json={"capability": "chat.turns"})
    assert resp.status_code == 403, resp.text


def test_missing_capability_field_is_422(con):
    resp = con.client.post(VERIFY, json={"requested": 1}, headers=con.h)
    assert resp.status_code == 422, resp.text


def test_every_answer_is_enforcement_unavailable_KNOWN_BUG(con):
    """KNOWN BUG (module docstring): undefined ``tenant_id`` → fail-closed deny for
    everything. When fixed, chat.turns must answer allowed=true on both tiers and
    forge.create allowed=true only for the member."""
    def ask(capability):
        resp = con.client.post(VERIFY, json={"capability": capability}, headers=con.h)
        assert resp.status_code == 200, resp.text
        return resp.json()

    for capability in ("chat.turns", "compute.run", "forge.create", "a2a.network",
                       "no.such.capability"):
        free = ask(capability)
        with member_tier():
            member = ask(capability)
        for data in (free, member):
            assert data == {
                "allowed": False, "capability": capability, "requested": 1,
                "tier": "free", "quota_remaining": None,
                "reason": "enforcement_unavailable", "upgrade_url": None,
            }


def test_body_tier_field_cannot_elevate(con):
    """The request's ``tier`` is never honoured — the tier comes from the licence."""
    resp = con.client.post(VERIFY, json={"capability": "forge.create", "tier": "member"},
                           headers=con.h)
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False and resp.json()["tier"] == "free"


def test_capability_decisions_never_reach_the_audit_chain_KNOWN_BUG(con):
    """KNOWN BUG: ``_audit_capability_decision`` imports ``forge.audit`` (no such
    module) and treats ``tenant_audit_chain()`` (which returns a Path) as a writer,
    then swallows the error. ``license.capability_decision`` is also absent from
    EVENT_SEVERITY / _EVENT_ALLOWLIST. Denials — here a real 402 from G3 — leave no
    decision record; only the route's own events land in the chain."""
    resp = con.client.post("/v1/console/panels",
                           json={"id": "x", "title": "x", "html": "<p/>"}, headers=con.h)
    assert resp.status_code == 402
    with pytest.raises(capability_api.LicenseDenied):
        capability_api.require_capability("forge.create", tenant_id="_default",
                                          entry_point="test")
    chain = con.audit_chain()
    text = chain.read_text() if chain.exists() else ""
    assert "license.capability_decision" not in text
