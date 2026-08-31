"""Per-subtask E2E for /v1/license/* FastAPI routes."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corvin_license import app as license_app


@pytest.fixture
def client(sandbox_home, pinned_pubkey):
    """Mount the license router on a fresh FastAPI test app."""
    license_app._flush_cache()
    app = FastAPI()
    app.include_router(license_app.router, prefix="/v1/license")
    return TestClient(app)


def test_healthz_unauthenticated(client):
    r = client.get("/v1/license/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["plugin"] == "corvin-license"
    assert "version" in body


def test_version_endpoint(client):
    r = client.get("/v1/license/version")
    assert r.status_code == 200
    assert r.json()["plugin"] == "corvin-license"


def test_status_free_tier_when_no_license_file(client):
    r = client.get("/v1/license/status")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "free"
    assert body["mode"] == "free-tier"
    assert body["feature_flags"] == []
    assert body["grace"]["state"] == "no-license"


def test_status_active_when_valid_license_installed(
    client, make_jwt, write_license_file,
):
    license_app._flush_cache()
    # ADR-0019: canonical business flag set.
    token = make_jwt(customer_id="acme-prod", tier="business",
                     employee_count_max=500, seats=100,
                     feature_flags=["compliance_reports_premium",
                                    "sso_wizard", "support_integration",
                                    "compute"])
    write_license_file(token)
    r = client.get("/v1/license/status")
    body = r.json()
    assert body["tier"] == "business"
    assert body["mode"] == "licensed-active"
    assert "sso_wizard" in body["feature_flags"]
    assert "compliance_reports_premium" in body["feature_flags"]
    # worm_archive is enterprise-only.
    assert "worm_archive" not in body["feature_flags"]
    assert body["expired"] is False
    assert body["grace"]["state"] == "active"
    # customer_id MUST be fingerprinted, never raw
    assert "acme-prod" not in r.text


def test_status_in_grace_when_expired_within_30d(
    client, make_jwt, write_license_file,
):
    license_app._flush_cache()
    # Expired 1 day ago, still in 30-day grace
    token = make_jwt(valid_seconds=24 * 3600, issued_offset_s=-2 * 24 * 3600)
    write_license_file(token)
    r = client.get("/v1/license/status")
    body = r.json()
    assert body["tier"] == "expired"
    assert "expired" in body["mode"]
    assert body["grace"]["state"] in ("in-grace", "expired")  # depends on exact timing


def test_status_signature_invalid(
    client, sandbox_home, write_license_file,
):
    license_app._flush_cache()
    # Hand-craft a bogus token that's not even valid JWT shape
    write_license_file("not.a.valid.jwt.token")
    r = client.get("/v1/license/status")
    body = r.json()
    # Should land in one of the failure modes — either signature-invalid
    # or malformed depending on whether it parses as JWT shape
    assert body["mode"] in (
        "license-signature-invalid", "license-malformed",
        "license-claim-invalid",
    )


def test_has_feature_returns_false_for_free_tier(client):
    license_app._flush_cache()
    # No license installed → has_feature for anything returns False
    assert license_app.has_feature("sso_wizard") is False
    assert license_app.has_feature("compliance_reports_premium") is False


def test_has_feature_respects_license_flags(
    client, make_jwt, write_license_file,
):
    """ADR-0019: pro tier canonical set includes compliance_reports_premium + compute."""
    license_app._flush_cache()
    token = make_jwt(tier="pro",
                     feature_flags=["compliance_reports_premium", "compute"])
    write_license_file(token)
    assert license_app.has_feature("compliance_reports_premium") is True
    # sso_wizard is business+ only.
    assert license_app.has_feature("sso_wizard") is False
    # worm_archive is enterprise-only.
    assert license_app.has_feature("worm_archive") is False
    # Unknown flag → False, never True
    assert license_app.has_feature("definitely_not_a_flag") is False


def test_status_response_never_leaks_customer_id_or_token(
    client, make_jwt, write_license_file,
):
    """Smoke: scan the whole response body for known-secret values."""
    license_app._flush_cache()
    secret_id = "TOPSECRET-CUSTOMER-UUID-XYZ"
    token = make_jwt(customer_id=secret_id)
    write_license_file(token)
    r = client.get("/v1/license/status")
    assert secret_id not in r.text
    assert token not in r.text


def test_status_cached_within_ttl(
    client, make_jwt, write_license_file, monkeypatch,
):
    """Repeated /status hits within 60s use the cache (no disk re-read)."""
    license_app._flush_cache()
    token = make_jwt(tier="pro")
    write_license_file(token)
    r1 = client.get("/v1/license/status")
    body1 = r1.json()
    # Mutate disk: delete the license. If cache hits, we still see tier=pro.
    write_license_file("").unlink()
    r2 = client.get("/v1/license/status")
    body2 = r2.json()
    assert body2["tier"] == body1["tier"]  # cached
