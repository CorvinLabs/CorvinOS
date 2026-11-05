"""Console licence routes (``/v1/console/license/*``) over HTTP, in-process.

The previous version assigned ``rec.tier = …`` on the frozen ``SessionRecord``
(FrozenInstanceError before any request) — and a non-persisted change would not
have reached ``require_session`` anyway, which loads the record from disk. Non-
owner sessions are now real persisted records (``Console.session_with_tier``).

Current, verified behaviour on a build without the ``corvin_license`` plugin:
  * ``/info`` — any authenticated session (documented: the UI renders feature
    gates on every page), reads ``corvin_operator.license``.
  * ``/status`` and ``/audit-tail`` — owner-only, then 503 "License plugin not
    installed" (``_check_license_plugin``). The legacy ``corvin_license`` package
    it imports from ``core/license/`` does not exist in this repo (nor in
    Corvin-Marketplace), so on every build these two routes can only answer 503.
  * ``POST /key`` — owner + CSRF; a key that does not verify is 400 and writes
    nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import console  # noqa: E402

BASE = "/v1/console/license"


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


def test_info_free_tier(con):
    resp = con.client.get(f"{BASE}/info")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tier"] == "free" and data["loaded"] is False
    assert data["expires_at"] is None and data["jti_prefix"] is None
    from corvin_operator.license.limits import FREE_TIER

    assert data["free_tier"].keys() == dict(FREE_TIER).keys()
    assert data["limits"]["compute_units_per_day"] == FREE_TIER["compute_units_per_day"]
    assert any(e.get("event_type") == "console.action_performed" for e in con.audit_events())


def test_info_reports_the_resolved_member_tier(con):
    import corvin_console.routes.license as license_route

    with mock.patch.object(license_route, "_lic_active_tier", lambda: "member"), \
         mock.patch.object(license_route, "_lic_is_loaded", lambda: True):
        data = con.client.get(f"{BASE}/info").json()
    assert data["tier"] == "member" and data["loaded"] is True


def test_info_is_open_to_any_authenticated_session_but_not_anonymous(con):
    viewer, _ = con.session_with_tier("viewer")
    assert viewer.get(f"{BASE}/info").status_code == 200
    assert con.anonymous().get(f"{BASE}/info").status_code == 401


@pytest.mark.parametrize("path", ["status", "audit-tail"])
def test_owner_only_reads(con, path):
    viewer, _ = con.session_with_tier("viewer")
    assert viewer.get(f"{BASE}/{path}").status_code == 403
    assert con.anonymous().get(f"{BASE}/{path}").status_code == 401
    owner = con.client.get(f"{BASE}/{path}")
    if path == "audit-tail":
        # reads the optional Enterprise plugin's audit file: honestly 503 without it
        assert owner.status_code == 503, owner.text
        assert owner.json()["detail"] == "License plugin not installed"
    else:
        # F-12: /status answers from the corvin_operator licence key; it must NOT 503
        # just because the optional Enterprise corvin_license plugin is absent.
        assert owner.status_code == 200, owner.text
        body = owner.json()
        assert body["tier"] == "free" and body["mode"] == "free", body


def test_apply_key_requires_csrf(con):
    resp = con.client.post(f"{BASE}/key", json={"key": "CORVIN-test-key-0000"})
    assert resp.status_code == 403, resp.text


def test_apply_key_requires_owner(con):
    viewer, viewer_csrf = con.session_with_tier("viewer")
    resp = viewer.post(f"{BASE}/key", json={"key": "CORVIN-test-key-0000"},
                       headers={"X-CSRF-Token": viewer_csrf})
    assert resp.status_code == 403, resp.text
    denied = [e for e in con.audit_events() if e.get("event_type") == "console.action_denied"]
    assert any(e.get("details", {}).get("action") == "license.key_apply" for e in denied)


def _fingerprint(con) -> str:
    return con.client.get("/v1/console/auth/whoami").json()["fingerprint"]


def test_apply_key_refuses_an_empty_or_wrong_reauth_token(con):
    for body in ({"key": "CORVIN-test-key-0000"},
                 {"key": "CORVIN-test-key-0000", "re_auth_token": ""},
                 {"key": "CORVIN-test-key-0000", "re_auth_token": "not-the-fingerprint"}):
        resp = con.client.post(f"{BASE}/key", json=body, headers=con.h)
        assert resp.status_code == 401, (body, resp.text)
    assert not list(con.home.rglob("license.key"))
    failed = [e for e in con.audit_events() if e.get("event_type") == "console.action_failed"
              and e.get("details", {}).get("reason") == "reauth-failed"]
    assert failed


def test_apply_unverifiable_key_is_rejected_and_not_written(con):
    resp = con.client.post(f"{BASE}/key", json={"key": "CORVIN-test-key-0000",
                                                 "re_auth_token": _fingerprint(con)}, headers=con.h)
    assert resp.status_code == 400, resp.text
    assert "signature verification failed" in resp.json()["detail"]
    assert not list(con.home.rglob("license.key"))
    assert con.client.get(f"{BASE}/info").json()["tier"] == "free"


def _owner_session_for_tenant(tenant_id: str, con):
    """A real persisted owner session whose record names ``tenant_id``."""
    import dataclasses

    from corvin_console import auth
    from fastapi.testclient import TestClient

    rec = auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    rec = dataclasses.replace(rec, tenant_id=tenant_id)
    auth._write_record(rec)
    client = TestClient(con.client.app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    return client


def test_audit_tail_validates_the_tenant_with_the_canonical_fullmatch(con, tmp_path):
    """``/audit-tail`` builds a file path from the session's tenant. It used a private
    ``re.compile(...).match`` whose ``$`` also matches before a trailing newline, so
    ``"_default\\n"`` passed and named a different file. It now calls
    ``core.tenants.validate_tenant_id`` (fullmatch since a0b71c4cb)."""
    import json as _json

    import corvin_console.routes.license as license_route

    reads: list[str] = []

    def fake_tail_path(tenant_id: str) -> Path:
        reads.append(tenant_id)
        p = tmp_path / "tails" / repr(tenant_id) / "tail.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"timestamp": 1, "event_type": "license.loaded",
                                  "details": {"tier": "member"}}) + "\n", encoding="utf-8")
        return p

    with mock.patch.object(license_route, "_verifier", object()), \
         mock.patch.object(license_route.console_audit, "_audit_path", fake_tail_path):
        # Positive control: the valid tenant reaches the file and gets its event back.
        ok = con.client.get(f"{BASE}/audit-tail")
        assert ok.status_code == 200, ok.text
        assert [e["event_type"] for e in ok.json()] == ["license.loaded"]
        assert reads == ["_default"]

        bad = _owner_session_for_tenant("_default\n", con).get(f"{BASE}/audit-tail")
        if bad.status_code == 200:
            # The session layer let the record through: the route itself must refuse
            # to build a path from it.
            assert bad.json() == []
        else:
            assert bad.status_code in (401, 403), bad.text
    # The newline tenant never reached path construction.
    assert reads == ["_default"], reads
