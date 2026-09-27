"""HTTP status contract of ``routes/license_gates.py`` (ADR-0701).

``require_capability`` signals a denial by RAISING ``LicenseDenied``. Until
2026-09-27 both gates caught it — and the ``HTTPException(402)`` they raised
themselves — in one ``except (..., Exception)`` and answered 500, so a free-tier
operator saw "internal error" instead of the upgrade prompt. These tests drive
a real FastAPI route through ``TestClient`` with the gate as a dependency.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from corvin_console import auth as session_auth
from corvin_console.routes import license_gates
from corvin_operator.license.capability_api import (
    CapabilityDecision,
    Decision,
    LicenseDenied,
    Tier,
)


def _client(monkeypatch, behaviour):
    monkeypatch.setattr(license_gates, "require_capability", behaviour)
    app = FastAPI()

    @app.post("/forge")
    async def forge(rec=Depends(license_gates.require_forge_capability)):
        return {"ok": True, "tenant": rec.tenant_id}

    @app.post("/publish")
    async def publish(rec=Depends(license_gates.require_marketplace_capability)):
        return {"ok": True}

    rec = session_auth.SessionRecord.__new__(session_auth.SessionRecord)
    object.__setattr__(rec, "tenant_id", "_default")
    app.dependency_overrides[license_gates.get_session] = lambda: rec
    return TestClient(app, raise_server_exceptions=False)


def _deny(capability, requested=1, *, tenant_id, entry_point):
    raise LicenseDenied(capability=capability, tier=Tier.FREE, reason="not_available_in_tier")


def _allow_unlimited(capability, requested=1, *, tenant_id, entry_point):
    return CapabilityDecision(
        decision=Decision.ALLOW, tier=Tier.MEMBER, capability=capability,
        requested=requested, allowed=None,
    )


def _unavailable(capability, requested=1, *, tenant_id, entry_point):
    return CapabilityDecision(
        decision=Decision.ENFORCEMENT_UNAVAILABLE, tier=Tier.FREE, capability=capability,
        requested=requested, allowed=0, reason="invalid_tenant",
    )


def _boom(*a, **k):
    raise RuntimeError("license store corrupted: /secret/path")


@pytest.mark.parametrize("path", ["/forge", "/publish"])
def test_denied_capability_is_402_not_500(monkeypatch, path):
    r = _client(monkeypatch, _deny).post(path)
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "license_required"
    assert detail["reason"] == "not_available_in_tier"


@pytest.mark.parametrize("path", ["/forge", "/publish"])
def test_unlimited_member_tier_passes(monkeypatch, path):
    # allowed=None means "unlimited" — it must not be read as a falsy deny.
    r = _client(monkeypatch, _allow_unlimited).post(path)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("behaviour", [_unavailable, _boom])
def test_enforcement_failure_is_fail_closed_503_without_leak(monkeypatch, behaviour):
    r = _client(monkeypatch, behaviour).post("/forge")
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error"] == "license_enforcement_unavailable"
    assert "/secret/path" not in r.text
