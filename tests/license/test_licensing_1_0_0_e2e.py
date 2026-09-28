"""Licensing 1.0.0 — the capability-verification endpoint, over HTTP, in-process.

The previous version of this file (and of the gate3/gate4 files) sent ``requests``
to a live service on ``localhost:8765``. Tests never talk to a running install;
this drives the real console router in a scratch CORVIN_HOME instead.

Where the endpoint lives
------------------------
``routes/licensing_verify.py`` is included in the console router, which both
hosts mount under ``/v1/console``: the URL is ``/v1/console/licensing/verify``.
Until 2026-09-27 the router carried its own ``/v1/licensing`` prefix and was
reachable only at the doubled ``/v1/console/v1/licensing/verify``.

What it answers
---------------
Until 2026-09-27 ``verify_capability`` passed an undefined ``tenant_id`` (it
never took the session); the NameError was swallowed by the fail-closed
``except`` and EVERY request answered ``allowed: false, reason:
"enforcement_unavailable"``. It now resolves the tenant from the session and
answers the real decision; the body's ``tier`` is still never honoured.
"""
from __future__ import annotations

import sys
from pathlib import Path

from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console, member_tier  # noqa: E402

import corvin_operator.license.capability_api as capability_api  # noqa: E402

VERIFY = "/v1/console/licensing/verify"


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def test_endpoint_is_served_under_the_console_prefix_only(con):
    body = {"capability": "chat.turns"}
    assert con.client.post("/v1/licensing/verify", json=body, headers=con.h).status_code == 404
    assert con.client.post("/v1/console/v1/licensing/verify", json=body, headers=con.h).status_code == 404
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


def _ask(con, capability, **extra):
    resp = con.client.post(VERIFY, json={"capability": capability, **extra}, headers=con.h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_answers_the_real_decision_per_tier(con):
    """Was KNOWN BUG: every answer was enforcement_unavailable."""
    assert _ask(con, "chat.turns") == {
        "allowed": True, "capability": "chat.turns", "requested": 1, "tier": "free",
        "quota_remaining": None, "reason": None, "upgrade_url": None,
    }
    free_forge = _ask(con, "forge.create")
    assert free_forge["allowed"] is False and free_forge["tier"] == "free"
    assert free_forge["reason"] == "not_available_in_tier"
    assert _ask(con, "compute.run")["allowed"] is True           # free allowance 10
    assert _ask(con, "compute.run", requested=11)["reason"] == "quota_exceeded"
    assert _ask(con, "no.such.capability")["reason"] == "unknown_capability"
    with member_tier():
        for capability in ("forge.create", "a2a.network", "compute.run"):
            data = _ask(con, capability, requested=10**9)
            assert data["allowed"] is True and data["tier"] == "member", data
            assert data["quota_remaining"] is None                # unlimited


@pytest.mark.parametrize("requested", [0, -1])
def test_non_positive_requested_is_422(con, requested):
    resp = con.client.post(VERIFY, json={"capability": "compute.run", "requested": requested},
                           headers=con.h)
    assert resp.status_code == 422, resp.text


def test_body_tier_field_cannot_elevate(con):
    """The request's ``tier`` is never honoured — the tier comes from the licence."""
    resp = con.client.post(VERIFY, json={"capability": "forge.create", "tier": "member"},
                           headers=con.h)
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False and resp.json()["tier"] == "free"


def test_capability_decisions_reach_the_tenant_audit_chain(con):
    """Was KNOWN BUG: ``_audit_capability_decision`` imported ``forge.audit`` (no
    such module) and swallowed the error — no decision was ever recorded. A G3
    denial (402), a direct deny and a verify-endpoint allow are all on the chain."""
    resp = con.client.post("/v1/console/panels",
                           json={"id": "x", "title": "x", "html": "<p/>"}, headers=con.h)
    assert resp.status_code == 402
    with pytest.raises(capability_api.LicenseDenied):
        capability_api.require_capability("forge.create", tenant_id="_default",
                                          entry_point="test")
    _ask(con, "chat.turns")
    decisions = [e for e in con.audit_events()
                 if e.get("event_type") == "license.capability_decision"]
    got = [(d["details"]["capability"], d["details"]["decision"], d["details"]["entry_point"])
           for d in decisions]
    assert got == [
        ("forge.create", "deny", "console:routes:forge"),
        ("forge.create", "deny", "test"),
        ("chat.turns", "allow", "http:licensing_verify"),
    ]
    for d in decisions:
        assert d["details"]["tenant_id"] == "_default" and d["details"]["tier"] == "free"
        assert d.get("hash") and d["details"].get("lom_bound") is True


def test_an_unrecorded_decision_does_not_change_the_verdict(con):
    """A chain write that does not commit is logged, not turned into a deny:
    write_event refuses records for a tenant other than the process tenant, so a
    write-dependent grant would lock member tenants out (see require_capability)."""
    with member_tier(), mock.patch.object(capability_api, "_audit_capability_decision",
                                          return_value=False) as audit:
        assert _ask(con, "forge.create")["allowed"] is True
        assert _ask(con, "no.such.capability")["allowed"] is False
    assert audit.call_count == 2
