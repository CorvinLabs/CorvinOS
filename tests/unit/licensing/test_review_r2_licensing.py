"""Refutation round of the 2026-11-19 licensing review (R2-CON-3, -4, -7).

Each test names the defect it pins. The route tests drive the real FastAPI
router over HTTP (TestClient); only the session dependency is replaced.

License: Apache-2.0
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.licensing.a2a_verifier import (
    CRL_MARKER,
    A2ADelegationVerifier,
    RevocationList,
    RevocationListError,
)
from core.licensing.authority_server import AuthorityServer
from core.licensing.member_credential import RSAKeyPair

from .test_review_2026_11_19_licensing import _now_iso, _signed_task  # noqa: F401


@pytest.fixture
def home(tmp_path):
    return tmp_path / "tenant-root"


def _issue(home, member="alice", tier="enterprise"):
    authority = AuthorityServer(str(home))
    verifier = A2ADelegationVerifier(str(home))
    res = authority.issue_credential(member, tier)
    assert res.success, res.error_message
    return authority, verifier, res.credential, res.private_key_pem


# ------------------------------------------------ (a) rollback after downgrade

def test_old_credential_file_copied_back_after_a_downgrade_stays_refused(home):
    authority, verifier, cred, key = _issue(home)
    old_file = authority.credential_store._path(cred.credential_id)
    saved = old_file.read_bytes()                       # valid enterprise credential
    assert verifier.verify_signed_task(_signed_task(key, cred, task_id="before")).is_valid

    res = authority.downgrade_tier(cred.credential_id, "free")
    assert res.success, res.error_message
    new = res.credential
    assert new.credential_id != cred.credential_id
    assert new.license_tier == "free"
    assert new.public_key_pem == cred.public_key_pem and new.expires_at == cred.expires_at
    assert authority.revocation_list.is_revoked(cred.credential_id)

    # The rollback: put the old, authority-signed enterprise file back.
    old_file.write_bytes(saved)
    rolled = verifier.verify_signed_task(_signed_task(key, cred, task_id="rollback"))
    assert rolled.is_valid is False
    assert "revoked" in rolled.error_message.lower()

    # ... and over the NEW id it does not verify either (the id is signed).
    new_file = authority.credential_store._path(new.credential_id)
    body = json.loads(saved)
    body["credential_id"] = new.credential_id
    new_file.write_text(json.dumps(body), encoding="utf-8")
    swapped = verifier.verify_signed_task(
        _signed_task(key, cred, task_id="swap", credential_id=new.credential_id))
    assert swapped.is_valid is False


def test_a_revoked_credential_cannot_be_downgraded_into_a_fresh_one(home):
    authority, _verifier, cred, _key = _issue(home)
    assert authority.revoke_credential(cred.credential_id, "compromised")
    res = authority.downgrade_tier(cred.credential_id, "member")
    assert res.success is False and "revoked" in res.error_message


# ------------------------------------------- (b) no laundering through re-sign

def _tamper(authority, cred, **changes):
    f = authority.credential_store._path(cred.credential_id)
    body = json.loads(f.read_text(encoding="utf-8"))
    body.update(changes)
    f.write_text(json.dumps(body), encoding="utf-8")


def test_renew_refuses_an_edited_credential(home):
    authority, verifier, cred, _key = _issue(home, "bob", "free")
    evil = RSAKeyPair.generate()
    _tamper(authority, cred, license_tier="enterprise", public_key_pem=evil.public_pem().decode())
    before = {p.name for p in authority.credential_store.credentials_dir.iterdir()}

    res = authority.renew_credential(cred.credential_id, 30)
    assert res.success is False
    assert "signature" in res.error_message
    assert {p.name for p in authority.credential_store.credentials_dir.iterdir()} == before


def test_downgrade_refuses_an_edited_credential(home):
    authority, _verifier, cred, _key = _issue(home, "bob", "member")
    evil = RSAKeyPair.generate()
    # Raise the tier and swap the key, then "downgrade" to member: the result
    # would be a freshly signed member credential bound to the attacker's key.
    _tamper(authority, cred, license_tier="enterprise", public_key_pem=evil.public_pem().decode())
    res = authority.downgrade_tier(cred.credential_id, "member")
    assert res.success is False
    assert "signature" in res.error_message
    assert not authority.revocation_list.is_revoked(cred.credential_id)


def test_renew_of_an_untouched_credential_still_works(home):
    authority, verifier, cred, key = _issue(home, "carol", "member")
    res = authority.renew_credential(cred.credential_id, 30)
    assert res.success, res.error_message
    assert verifier.verify_signed_task(_signed_task(key, res.credential, task_id="renewed")).is_valid


def test_signature_check_never_creates_an_authority_key(home):
    authority = AuthorityServer(str(home))
    from core.licensing.member_credential import MemberCredential
    stray = MemberCredential(
        member_id="dave", license_tier="member", issued_at=_now_iso(),
        expires_at=_now_iso(3600), public_key_pem="x", signature="00",
    )
    assert authority.signature_valid(stray) is False
    assert not (home / "licensing" / "authority").exists()


# ------------------------------------------------- (c) deleted CRL fails closed

def test_deleting_the_crl_does_not_lift_the_revocations(home):
    authority, verifier, cred, key = _issue(home)
    crl_file = home / "licensing" / "crl.json"
    assert crl_file.exists(), "the first issuance initialises the list"
    assert (home / "licensing" / CRL_MARKER).exists()
    # The verifier was built BEFORE any list existed and is not used until
    # the list is gone (R3-CON-1): an instance that never saw crl.json
    # compared "no file" with its own "never loaded" and kept its empty list,
    # so the revoked credential verified. This test used to call verify
    # first, which made the instance load the file and hid exactly that.
    assert verifier.revocation_list._loaded_mtime is None, "positive control: never loaded"
    assert authority.revoke_credential(cred.credential_id, "x")

    crl_file.unlink()

    res = verifier.verify_signed_task(_signed_task(key, cred, task_id="r2"))
    assert res.is_valid is False                       # used to be True
    assert verifier.revocation_list.load_failed is True
    # A fresh instance (a restarted console) reads it the same way.
    fresh = RevocationList(str(home))
    assert fresh.load_failed is True and fresh.is_revoked("anyone") is True
    # Nothing recreates the list silently.
    with pytest.raises(RevocationListError):
        fresh.revoke("someone", "y")
    assert authority.issue_credential("erin", "member").success is False
    assert not crl_file.exists()


def test_a_store_that_never_issued_has_an_empty_list(home):
    crl = RevocationList(str(home))
    assert crl.load_failed is False and crl.is_revoked("alice") is False
    assert not (home / "licensing" / CRL_MARKER).exists()


def test_a_list_written_before_the_marker_existed_is_marked_on_first_read(home):
    (home / "licensing").mkdir(parents=True)
    crl_file = home / "licensing" / "crl.json"
    crl_file.write_text(json.dumps({"revoked": {"old": {"reason": "r"}}}), encoding="utf-8")
    crl = RevocationList(str(home))
    assert crl.is_revoked("old") and not crl.is_revoked("other")
    assert (home / "licensing" / CRL_MARKER).exists()
    crl_file.unlink()
    assert RevocationList(str(home)).is_revoked("other") is True


# ------------------------------ (d) credential info is not taken from the file

def test_credential_info_does_not_report_an_edited_credential_as_usable(home):
    authority, _verifier, cred, _key = _issue(home, "frank", "free")
    info = authority.get_credential_info(cred.credential_id)
    assert info["signature_valid"] is True and info["is_valid"] is True
    assert info["can_delegate"] is False                # free tier

    _tamper(authority, cred, license_tier="enterprise")
    info = authority.get_credential_info(cred.credential_id)
    assert info["signature_valid"] is False
    assert info["is_valid"] is False
    assert info["can_delegate"] is False                # used to be True


# ------------------------------------------------------------ console routes

@pytest.fixture
def client(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from corvin_console import deps
    from corvin_console.routes import a2a_licensing_gate_routes as routes

    routes._services.clear()
    state = {"rec": None}

    def _rec():
        if state["rec"] is None:
            raise fastapi.HTTPException(status_code=401, detail="no session")
        return state["rec"]

    app = fastapi.FastAPI()
    app.include_router(routes.router)
    for dep in (deps.require_session, deps.require_csrf, deps.require_session_csrf_on_mutation):
        app.dependency_overrides[dep] = _rec

    def login(tenant_id="_default", tier="owner", internal=False):
        state["rec"] = SimpleNamespace(tenant_id=tenant_id, tier=tier, is_internal_tool=internal)

    http = TestClient(app)
    http.login = login
    http.routes = routes
    yield http
    routes._services.clear()


ISSUE = "/v1/licensing/a2a/credential/issue"


def test_info_route_reports_the_signature_state(client):
    client.login()
    cred_id = client.post(ISSUE, json={"member_id": "gina", "license_tier": "member"}).json()["credential_id"]
    info = client.get(f"/v1/licensing/a2a/credential/{cred_id}").json()
    assert info["signature_valid"] is True and info["can_delegate"] is True

    _verifier, authority = client.routes._tenant_services(SimpleNamespace(tenant_id="_default"))
    f = authority.credential_store._path(cred_id)
    body = json.loads(f.read_text(encoding="utf-8"))
    body["license_tier"] = "enterprise"
    f.write_text(json.dumps(body), encoding="utf-8")
    info = client.get(f"/v1/licensing/a2a/credential/{cred_id}").json()
    assert info["signature_valid"] is False and info["can_delegate"] is False


@pytest.mark.parametrize("tenant", ["test", "local", "admin", "system", "internal", "service", "operator"])
def test_tenants_the_canonical_resolver_admits_are_served(client, tenant):
    """R2-CON-7: ``core.tenants`` reserves these names, ``forge.tenants`` (the
    canonical resolver) admits them - such a tenant's session got 403."""
    from forge.tenants import validate_tenant_id
    assert validate_tenant_id(tenant) == tenant         # positive control
    client.login(tenant_id=tenant)
    r = client.get("/v1/licensing/a2a/crl")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("tenant", ["..", "a/b", "Acme", "__x", ""])
def test_tenants_the_canonical_resolver_refuses_stay_refused(client, tenant):
    client.login(tenant_id=tenant)
    assert client.get("/v1/licensing/a2a/crl").status_code == 403


def test_handlers_run_off_the_event_loop(client, monkeypatch):
    """R2-CON-4: every handler held the event loop for a chain write, an flock
    and an RSA key generation."""
    import asyncio

    seen = []

    def probe(name):
        try:
            asyncio.get_running_loop()
            seen.append((name, "ON-LOOP"))
        except RuntimeError:
            seen.append((name, threading.current_thread().name))

    from core.licensing import authority_server, a2a_verifier
    real_issue = authority_server.AuthorityServer.issue_credential
    real_audit = a2a_verifier.A2ADelegationVerifier.get_verification_audit
    monkeypatch.setattr(authority_server.AuthorityServer, "issue_credential",
                        lambda self, *a, **k: (probe("issue"), real_issue(self, *a, **k))[1])
    monkeypatch.setattr(a2a_verifier.A2ADelegationVerifier, "get_verification_audit",
                        lambda self, *a, **k: (probe("audit"), real_audit(self, *a, **k))[1])
    client.login()
    assert client.post(ISSUE, json={"member_id": "hank", "license_tier": "member"}).status_code == 200
    assert client.get("/v1/licensing/a2a/audit").status_code == 200
    assert [n for n, _ in seen] == ["issue", "audit"]
    assert all(where != "ON-LOOP" for _, where in seen), seen


def test_no_route_handler_of_the_two_modules_is_async():
    """Structure behind the test above, for both modules of R2-CON-4."""
    routes = Path(__file__).resolve().parents[3] / "core/console/corvin_console/routes"
    for name in ("a2a_licensing_gate_routes.py", "cost_insights_routes.py"):
        tree = ast.parse((routes / name).read_text(encoding="utf-8"))
        handlers = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any("router." in ast.unparse(d) for d in n.decorator_list)
        ]
        assert len(handlers) >= 6, (name, len(handlers))  # positive control
        assert [h.name for h in handlers if isinstance(h, ast.AsyncFunctionDef)] == [], name
