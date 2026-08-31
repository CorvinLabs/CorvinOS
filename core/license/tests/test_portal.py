"""ADR-0019 §Phase 1 — customer self-service portal endpoint.

The /v1/license/me route returns the installed license.jwt to a
bearer-authenticated caller. Tests cover the four documented
outcomes (200 / 401 missing / 401 invalid / 403 disabled / 404
no-license) plus the audit-event allow-list.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from corvin_license import app as license_app
from corvin_license import portal as _portal


def _wrap_router_as_app():
    """The license-plugin exports an APIRouter; wrap it in a FastAPI
    app for TestClient."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(license_app.router)
    return app


@pytest.fixture
def client():
    return TestClient(_wrap_router_as_app())


def _read_chain_events(home: Path) -> list[dict]:
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [
        json.loads(line) for line in chain.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_portal_disabled_returns_403(sandbox_home, monkeypatch, client):
    """Without the env var configured, portal returns 403 on every request."""
    monkeypatch.delenv("CORVIN_LICENSE_PORTAL_BEARER", raising=False)
    r = client.get("/me", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "portal-disabled"

    events = _read_chain_events(sandbox_home)
    denied = [e for e in events if e["event_type"] == "license.portal_denied"]
    assert len(denied) >= 1
    assert denied[-1]["details"]["reason"] == "portal-disabled"


def test_portal_missing_bearer_returns_401(sandbox_home, monkeypatch, client):
    """Portal enabled, no Authorization header → 401."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "a" * 32)
    r = client.get("/me")
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "missing-bearer"
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


def test_portal_invalid_bearer_returns_401(sandbox_home, monkeypatch, client):
    """Portal enabled, wrong bearer → 401."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "a" * 32)
    r = client.get("/me", headers={"Authorization": "Bearer " + "b" * 32})
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "invalid-bearer"


def test_portal_no_license_returns_404(sandbox_home, monkeypatch, client):
    """Portal enabled + correct bearer + no license installed → 404."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "a" * 32)
    r = client.get("/me", headers={"Authorization": "Bearer " + "a" * 32})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "no-license"


def test_portal_happy_path_returns_jwt_bytes(
    sandbox_home, pinned_pubkey, monkeypatch, make_jwt, write_license_file,
    client,
):
    """Portal enabled + correct bearer + license installed → 200 + token text."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "x" * 32)
    license_app._flush_cache()
    token = make_jwt(customer_id="acme-prod", tier="business")
    write_license_file(token)

    r = client.get("/me", headers={"Authorization": "Bearer " + "x" * 32})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "no-store" in r.headers.get("cache-control", "")
    assert r.text.strip() == token


def test_portal_logs_served_event_without_raw_customer_id(
    sandbox_home, pinned_pubkey, monkeypatch, make_jwt, write_license_file,
    client,
):
    """The audit event must carry only fingerprints, never raw customer-id."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "x" * 32)
    license_app._flush_cache()
    secret_id = "VERY-SECRET-CUSTOMER-UUID"
    token = make_jwt(customer_id=secret_id, tier="pro")
    write_license_file(token)

    r = client.get("/me", headers={"Authorization": "Bearer " + "x" * 32})
    assert r.status_code == 200

    events = _read_chain_events(sandbox_home)
    served = [e for e in events if e["event_type"] == "license.portal_served"]
    assert len(served) == 1
    blob = json.dumps(served[0])
    assert secret_id not in blob
    # bearer value must not appear either.
    assert "x" * 32 not in blob
    # but fingerprint MUST.
    assert len(served[0]["details"]["customer_fp"]) == 12
    assert len(served[0]["details"]["bearer_fp"]) == 12


def test_portal_bearer_with_wrong_length_rejected(
    sandbox_home, monkeypatch, client,
):
    """A 33-char bearer against a 32-char configured one is rejected
    cleanly (no length-leak via constant-time path)."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "a" * 32)
    r = client.get(
        "/me", headers={"Authorization": "Bearer " + "a" * 33},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "invalid-bearer"


def test_portal_disabled_when_bearer_too_short(
    sandbox_home, monkeypatch, client,
):
    """A configured bearer shorter than 16 chars is treated as not-set."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "tooshort")
    r = client.get(
        "/me", headers={"Authorization": "Bearer tooshort"},
    )
    # 403 portal-disabled wins because portal_enabled() returns False.
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "portal-disabled"


def test_portal_check_bearer_constant_time_compare(monkeypatch):
    """Unit-level: check_bearer returns False on wrong, True on right."""
    monkeypatch.setenv("CORVIN_LICENSE_PORTAL_BEARER", "secret-token-1234567890")
    assert _portal.check_bearer("secret-token-1234567890")
    assert not _portal.check_bearer("secret-token-WRONG-678")
    assert not _portal.check_bearer("")
    assert not _portal.check_bearer("differentlength")


def test_portal_disabled_returns_false_when_env_missing(monkeypatch):
    monkeypatch.delenv("CORVIN_LICENSE_PORTAL_BEARER", raising=False)
    assert not _portal.portal_enabled()
    assert not _portal.check_bearer("anything")
