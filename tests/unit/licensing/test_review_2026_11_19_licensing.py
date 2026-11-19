"""Regression tests for the 2026-11-19 licensing review (TEN-1 .. TEN-4).

Each test names the defect it pins. The route tests drive the real FastAPI
router over HTTP (TestClient); only the session dependency is replaced, because
the console's login is not the subject here.

License: Apache-2.0
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.licensing import audit as licensing_audit
from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    RevocationList,
    RevocationListError,
    TaskReplayStore,
)
from core.licensing.authority_server import AuthorityServer
from core.licensing.member_credential import (
    MAX_TASK_TTL_SECONDS,
    CredentialStore,
    InvalidLicensingId,
    MemberCredential,
    RSAKeyPair,
    SignedTask,
    task_signing_bytes,
    validate_credential_id,
    validate_member_id,
)


def _now_iso(delta_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _signed_task(private_pem: str, cred: MemberCredential, **over) -> SignedTask:
    task = SignedTask(
        task_id=over.pop("task_id", "task-1"),
        member_id=over.pop("member_id", cred.member_id),
        credential_id=over.pop("credential_id", cred.credential_id),
        operation=over.pop("operation", "delegate"),
        payload=over.pop("payload", {"a": 1}),
        signed_at=over.pop("signed_at", _now_iso()),
        signature="",
        ttl_seconds=over.pop("ttl_seconds", 600),
    )
    assert not over
    key = RSAKeyPair.from_private_pem(private_pem.encode())
    task.signature = key.sign(task_signing_bytes(task)).hex()
    return task


@pytest.fixture
def home(tmp_path):
    return tmp_path / "tenant-root"


@pytest.fixture
def issued(home):
    """(authority, verifier, credential, member private key PEM)."""
    authority = AuthorityServer(str(home))
    verifier = A2ADelegationVerifier(str(home))
    res = authority.issue_credential("alice", "member")
    assert res.success, res.error_message
    assert res.private_key_pem
    return authority, verifier, res.credential, res.private_key_pem


# ---------------------------------------------------------------- TEN-1: ids

TRAVERSAL = "../../../escaped"


@pytest.mark.parametrize("bad", [
    TRAVERSAL, "..", ".", "", "a/b", "a\\b", "alice\n", "x" * 65, "al ice", "a\x00b",
    "alice:12:zzzzzzzz", "alice:12:0123abcd:extra", None, 7,
])
def test_ids_are_fullmatched(bad):
    with pytest.raises(InvalidLicensingId):
        validate_member_id(bad)
    with pytest.raises(InvalidLicensingId):
        validate_credential_id(bad)


def test_valid_id_shapes():
    assert validate_member_id("alice.example-1_x") == "alice.example-1_x"
    assert validate_credential_id("alice") == "alice"
    assert validate_credential_id("alice:1700000000:0123abcd") == "alice:1700000000:0123abcd"


def test_traversal_member_id_cannot_build_a_credential():
    with pytest.raises(InvalidLicensingId):
        MemberCredential(
            member_id=TRAVERSAL, license_tier="member",
            issued_at=_now_iso(), expires_at=_now_iso(3600), public_key_pem="x",
        )


def test_store_refuses_traversal_and_writes_nothing_outside(home, tmp_path):
    store = CredentialStore(str(home))
    outside_before = sorted(p.name for p in tmp_path.rglob("*.json"))

    assert store.load(TRAVERSAL) is None
    with pytest.raises(InvalidLicensingId):
        store.delete(TRAVERSAL)
    with pytest.raises(InvalidLicensingId):
        store._path(TRAVERSAL)

    cred = MemberCredential(
        member_id="alice", license_tier="member",
        issued_at=_now_iso(), expires_at=_now_iso(3600), public_key_pem="x",
    )
    # A credential whose id was tampered with after construction.
    object.__setattr__(cred, "credential_id", TRAVERSAL)
    with pytest.raises(InvalidLicensingId):
        store.save(cred)

    assert sorted(p.name for p in tmp_path.rglob("*.json")) == outside_before
    assert not (tmp_path / "escaped.json").exists()


def test_delete_of_a_file_outside_the_store_is_refused(home, tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    store = CredentialStore(str(home))
    rel = os.path.relpath(victim.with_suffix(""), store.credentials_dir)
    with pytest.raises(InvalidLicensingId):
        store.delete(rel)
    assert victim.exists()


def test_store_is_private(home, issued):
    _authority, _verifier, cred, _key = issued
    store_dir = home / "licensing" / "credentials"
    assert stat.S_IMODE(store_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "licensing").stat().st_mode) == 0o700
    cred_file = store_dir / f"{cred.credential_id}.json"
    assert stat.S_IMODE(cred_file.stat().st_mode) == 0o600


def test_authority_refuses_traversal_member(home, tmp_path):
    res = AuthorityServer(str(home)).issue_credential(TRAVERSAL, "member")
    assert res.success is False
    assert not list(tmp_path.rglob("escaped*"))


# ---------------------------------------------------- TEN-2: verifier / tasks

def test_full_pass_then_replay_is_refused(issued):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred)
    first = verifier.verify_signed_task(task)
    assert first.is_valid, (first.checks_failed, first.error_message)
    assert first.license_tier == "member"

    second = verifier.verify_signed_task(task)
    assert second.is_valid is False
    assert "replay" in second.error_message.lower()


def test_replay_store_survives_a_new_verifier_instance(home, issued):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred, task_id="task-restart")
    assert verifier.verify_signed_task(task).is_valid
    assert A2ADelegationVerifier(str(home)).verify_signed_task(task).is_valid is False


@pytest.mark.parametrize("field,value", [
    ("task_id", "other-task"),
    ("ttl_seconds", 3599),
    ("operation", "other_op"),
    ("payload", {"a": 2}),
])
def test_signature_covers_task_id_ttl_operation_payload(issued, field, value):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred, task_id=f"sig-{field}")
    setattr(task, field, value)  # change after signing
    res = verifier.verify_signed_task(task)
    assert res.is_valid is False
    assert res.error_message == "Invalid task signature"


def test_signature_covers_credential_id(home, issued):
    authority, verifier, cred, key = issued
    # A second credential of the same member bound to the SAME public key.
    other = authority.issue_credential("alice", "member", public_key_pem=cred.public_key_pem)
    assert other.success
    task = _signed_task(key, cred, task_id="sig-cred")
    task.credential_id = other.credential.credential_id
    res = verifier.verify_signed_task(task)
    assert res.is_valid is False
    assert res.error_message == "Invalid task signature"


def test_ttl_is_capped_server_side(issued):
    _authority, verifier, cred, key = issued
    old = _now_iso(-(MAX_TASK_TTL_SECONDS + 120))
    # Honestly signed with a ten-year TTL: the signature is fine, the cap is not.
    task = _signed_task(key, cred, task_id="ttl-cap", signed_at=old, ttl_seconds=315_360_000)
    assert task.effective_ttl_seconds() == MAX_TASK_TTL_SECONDS
    res = verifier.verify_signed_task(task)
    assert res.is_valid is False
    assert res.error_message == "Task has expired"


@pytest.mark.parametrize("signed_at", ["not-a-date", "", None])
def test_unparseable_signed_at_is_expired(signed_at):
    task = SignedTask("t", "alice", "alice", "op", {}, signed_at, "", 600)
    assert task.is_expired() is True


def test_far_future_signed_at_is_refused(issued):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred, task_id="future", signed_at=_now_iso(24 * 3600))
    assert verifier.verify_signed_task(task).is_valid is False


def test_missing_authority_key_fails_closed(home, issued):
    _authority, verifier, cred, key = issued
    verifier.authority_public_key_file.unlink()
    res = verifier.verify_signed_task(_signed_task(key, cred, task_id="no-authority"))
    assert res.is_valid is False
    assert res.error_message == "Authority key unavailable"
    assert res.license_tier == "unknown"  # an unvouched tier is never reported


def test_self_signed_credential_is_refused(home, issued):
    """A credential written into the store without the authority's signature."""
    _authority, verifier, _cred, _key = issued
    rogue_key = RSAKeyPair.generate()
    rogue = MemberCredential(
        member_id="mallory", license_tier="enterprise",
        issued_at=_now_iso(), expires_at=_now_iso(86400),
        public_key_pem=rogue_key.public_pem().decode(),
    )
    from core.licensing.member_credential import credential_signing_bytes
    rogue.signature = rogue_key.sign(credential_signing_bytes(rogue)).hex()  # self-signed
    CredentialStore(str(home)).save(rogue)

    res = verifier.verify_signed_task(_signed_task(rogue_key.private_pem().decode(), rogue))
    assert res.is_valid is False
    assert res.error_message == "Invalid credential signature"


def test_edited_tier_in_credential_file_is_refused(home, issued):
    authority, verifier, _cred, _key = issued
    free = authority.issue_credential("bob", "free")
    assert free.success
    cred_file = home / "licensing" / "credentials" / f"{free.credential.credential_id}.json"
    data = json.loads(cred_file.read_text(encoding="utf-8"))
    data["license_tier"] = "enterprise"
    cred_file.write_text(json.dumps(data), encoding="utf-8")

    edited = CredentialStore(str(home)).load(free.credential.credential_id)
    res = verifier.verify_signed_task(_signed_task(free.private_key_pem, edited, task_id="edited"))
    assert res.is_valid is False
    assert res.error_message == "Invalid credential signature"


def test_cross_member_impersonation_is_refused(issued):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred, task_id="impersonate", member_id="bob")
    res = verifier.verify_signed_task(task)
    assert res.is_valid is False
    assert res.error_message == "Credential/member mismatch"


def test_malformed_ids_do_not_reach_disk_or_result(issued):
    _authority, verifier, cred, key = issued
    task = _signed_task(key, cred)
    task.credential_id = TRAVERSAL
    res = verifier.verify_signed_task(task)
    assert res.is_valid is False
    assert res.error_message == "Malformed identifier"
    assert TRAVERSAL not in json.dumps(res.to_dict())


def test_private_key_is_never_persisted_or_serialised(home, issued):
    authority, _verifier, _cred, key = issued
    marker = key.strip().splitlines()[1]
    for path in home.rglob("*"):
        if path.is_file() and path.name != "authority_key.pem":
            assert marker not in path.read_text(encoding="utf-8", errors="replace"), path
    res = authority.issue_credential("carol", "member")
    assert "private_key" not in json.dumps(res.to_dict())
    assert res.private_key_pem not in repr(res)


def test_submitted_public_key_means_no_private_key_on_the_host(home):
    member_key = RSAKeyPair.generate()
    res = AuthorityServer(str(home)).issue_credential(
        "dave", "member", public_key_pem=member_key.public_pem().decode())
    assert res.success and res.private_key_pem is None


# ------------------------------------------------------------- TEN-3: CRL

def test_corrupt_crl_revokes_everything(home, issued):
    _authority, verifier, cred, key = issued
    crl_file = home / "licensing" / "crl.json"
    crl_file.write_text("{ not json", encoding="utf-8")

    crl = RevocationList(str(home))
    assert crl.load_failed is True
    assert crl.is_revoked("anything") is True
    with pytest.raises(RevocationListError):
        crl.revoke("alice", "x")
    assert crl_file.read_text(encoding="utf-8") == "{ not json"  # never overwritten

    res = verifier.verify_signed_task(_signed_task(key, cred, task_id="corrupt-crl"))
    assert res.is_valid is False
    assert res.error_message == "Credential has been revoked"


@pytest.mark.parametrize("body", ['[]', '{"revoked": []}', '{"revoked": {"a": 1}}', '{}'])
def test_malformed_crl_shapes_fail_closed(home, body):
    (home / "licensing").mkdir(parents=True)
    (home / "licensing" / "crl.json").write_text(body, encoding="utf-8")
    assert RevocationList(str(home)).is_revoked("alice") is True


def test_crl_recovers_once_repaired(home):
    crl = RevocationList(str(home))
    crl.revoke("alice", "r")
    crl.crl_file.write_text("garbage", encoding="utf-8")
    assert crl.is_revoked("bob") is True
    crl.crl_file.write_text(json.dumps({"revoked": {}}), encoding="utf-8")
    assert crl.is_revoked("bob") is False


def test_crl_save_is_atomic_and_private(home, monkeypatch):
    crl = RevocationList(str(home))
    crl.revoke("alice", "first")
    before = crl.crl_file.read_bytes()
    assert stat.S_IMODE(crl.crl_file.stat().st_mode) == 0o600

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RevocationListError):
        crl.revoke("bob", "second")
    monkeypatch.undo()

    assert crl.crl_file.read_bytes() == before          # old file intact, not truncated
    assert "bob" not in crl.revoked                      # memory agrees with disk
    assert not [p for p in crl.crl_file.parent.iterdir() if p.name.endswith(".tmp")]
    assert RevocationList(str(home)).is_revoked("alice") is True


def test_revocation_is_seen_by_another_instance_immediately(home, issued):
    authority, verifier, cred, key = issued
    assert authority.revoke_credential(cred.credential_id, "compromised") is True
    res = verifier.verify_signed_task(_signed_task(key, cred, task_id="after-revoke"))
    assert res.is_valid is False


def test_replay_store_fails_closed_on_corruption(home):
    store = TaskReplayStore(str(home))
    store.file.write_text("[1,2", encoding="utf-8")
    assert store.claim("alice", "t1", 9e12) is False


# -------------------------------------------------------- TEN-4: authority

def test_downgrade_only_goes_down_and_stays_verifiable(home, issued):
    authority, verifier, cred, key = issued  # member
    for not_lower in ("member", "enterprise", "bogus"):
        res = authority.downgrade_tier(cred.credential_id, not_lower)
        assert res.success is False, not_lower
    assert CredentialStore(str(home)).load(cred.credential_id).license_tier == "member"

    res = authority.downgrade_tier(cred.credential_id, "free")
    assert res.success is True
    # A downgrade issues a NEW credential and revokes the old id (rollback
    # protection, see test_review_r2_licensing.py).
    assert res.credential.credential_id != cred.credential_id
    stored = CredentialStore(str(home)).load(res.credential.credential_id)
    assert stored.license_tier == "free"
    ok, code = verifier._verify_credential_signature(stored)   # signed by the authority
    assert ok, code

    denied = verifier.verify_signed_task(_signed_task(key, stored, task_id="after-downgrade"))
    assert denied.is_valid is False
    assert denied.error_message == "Insufficient license tier for A2A delegation"
    # no way back up, on either id
    assert authority.downgrade_tier(cred.credential_id, "member").success is False
    assert authority.downgrade_tier(stored.credential_id, "member").success is False


def test_authority_key_material_is_private(home, issued):
    key_dir = home / "licensing" / "authority"
    assert stat.S_IMODE(key_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((key_dir / "authority_key.pem").stat().st_mode) == 0o600


@pytest.mark.parametrize("days", [-1, 36501, 1.5, True, "90"])
def test_validity_days_is_bounded(home, days):
    assert AuthorityServer(str(home)).issue_credential("erin", "member", validity_days=days).success is False


def _chain_events(tenant_id="_default"):
    path = licensing_audit.chain_path(tenant_id)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if str(rec.get("event_type", "")).startswith("a2a.license_"):
            out.append(rec)
    return out


def test_lifecycle_is_recorded_in_the_tenant_audit_chain(home, issued):
    authority, verifier, cred, key = issued
    before = len(_chain_events())
    assert verifier.verify_signed_task(_signed_task(key, cred, task_id="audited")).is_valid
    assert authority.downgrade_tier(cred.credential_id, "free").success
    assert authority.revoke_credential(cred.credential_id, "secret free text reason") is True

    new = _chain_events()[before:]
    types = [r["event_type"] for r in new]
    assert types == [
        "a2a.license_task_verified",
        "a2a.license_credential_downgraded",
        "a2a.license_credential_revoked",   # the old id, revoked by the downgrade
        "a2a.license_credential_revoked",   # the explicit revocation below it
    ]
    blob = json.dumps(new)
    assert "secret free text reason" not in blob          # the reason is a length only
    assert new[1]["details"]["previous_cred_ref"] == cred.credential_id
    assert new[1]["details"]["cred_ref"] != cred.credential_id
    assert new[2]["details"]["source"] == "downgrade"
    assert new[2]["details"]["cred_ref"] == cred.credential_id
    assert new[3]["details"]["reason_len"] == len("secret free text reason")
    assert new[0]["details"]["cred_ref"] == cred.credential_id
    # No unchained side files anymore.
    assert not (home / "licensing" / "verification_audit.jsonl").exists()
    assert not (home / "licensing" / "issuance_ledger.jsonl").exists()


def test_issue_is_audit_first(home, monkeypatch):
    authority = AuthorityServer(str(home))

    def refuse(*_a, **_k):
        raise licensing_audit.LicensingAuditError("chain down")
    monkeypatch.setattr(licensing_audit, "emit", refuse)
    res = authority.issue_credential("frank", "member")
    assert res.success is False
    assert not list((home / "licensing" / "credentials").glob("*.json"))


def test_pass_without_an_audit_record_is_a_denial(issued, monkeypatch):
    _authority, verifier, cred, key = issued
    monkeypatch.setattr(licensing_audit, "emit", lambda *a, **k: False)
    res = verifier.verify_signed_task(_signed_task(key, cred, task_id="unaudited"))
    assert res.is_valid is False
    assert res.error_message == "Audit trail unavailable"


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
    yield http
    routes._services.clear()


ISSUE = "/v1/licensing/a2a/credential/issue"


def test_issue_route_requires_a_session(client):
    assert client.post(ISSUE, json={"member_id": "alice", "license_tier": "member"}).status_code == 401


@pytest.mark.parametrize("tier,internal", [("member", False), ("viewer", False), (None, False), ("owner", True)])
def test_issue_and_revoke_routes_are_owner_only(client, tier, internal):
    client.login(tier=tier, internal=internal)
    r = client.post(ISSUE, json={"member_id": "alice", "license_tier": "member"})
    assert r.status_code == 403
    r = client.post("/v1/licensing/a2a/credential/alice/revoke", json={"reason": "x"})
    assert r.status_code == 403


def test_issue_route_refuses_traversal_ids(client):
    client.login()
    r = client.post(ISSUE, json={"member_id": TRAVERSAL, "license_tier": "member"})
    assert r.status_code == 422
    assert client.get("/v1/licensing/a2a/credential/..%2F..%2Fescaped").status_code in (404, 422)
    assert client.get("/v1/licensing/a2a/member/a%0Ab/credentials").status_code == 422


def test_owner_issues_into_the_sessions_tenant_only(client):
    from core.paths.tenant import tenant_home

    client.login(tenant_id="_default")
    r = client.post(ISSUE, json={"member_id": "alice", "license_tier": "member"})
    assert r.status_code == 200, r.text
    body = r.json()
    cred_id = body["credential_id"]
    assert body["private_key_pem"].startswith("-----BEGIN PRIVATE KEY-----")

    assert (tenant_home("_default") / "licensing" / "credentials" / f"{cred_id}.json").exists()
    assert client.get(f"/v1/licensing/a2a/credential/{cred_id}").status_code == 200

    # Another tenant's owner sees neither the credential nor the member.
    client.login(tenant_id="acme-corp")
    assert client.get(f"/v1/licensing/a2a/credential/{cred_id}").status_code == 404
    listing = client.get("/v1/licensing/a2a/member/alice/credentials").json()
    assert listing["total"] == 0
    assert not (tenant_home("acme-corp") / "licensing" / "credentials" / f"{cred_id}.json").exists()


def test_verify_route_refuses_oversized_ttl(client):
    client.login()
    r = client.post("/v1/licensing/a2a/verify", json={
        "task_id": "t1", "member_id": "alice", "credential_id": "alice", "operation": "op",
        "payload": {}, "signed_at": _now_iso(), "signature": "00",
        "ttl_seconds": MAX_TASK_TTL_SECONDS + 1,
    })
    assert r.status_code == 422


def test_crl_route_reports_an_unreadable_list_honestly(client):
    from core.paths.tenant import tenant_home

    client.login()
    assert client.get("/v1/licensing/a2a/crl").status_code == 200
    (tenant_home("_default") / "licensing" / "crl.json").write_text("{", encoding="utf-8")
    assert client.get("/v1/licensing/a2a/crl").status_code == 503
