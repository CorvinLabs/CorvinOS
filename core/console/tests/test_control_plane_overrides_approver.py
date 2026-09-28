"""Override approver identity (adversarial review 2026-09-27).

``_approver_id`` used to return ``"token:" + rec.token_fingerprint`` — and
every session creator sets ``token_fingerprint=""``, so EVERY session of every
operator shared the single approver identity ``"token:"``: registering one
approver made every session an approver. It is now the non-secret
``rec.sid_fingerprint``; an empty fingerprint fails closed (403); and the
requesting session cannot decide its own request (four-eyes).
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

BASE = "/v1/console/control-plane/overrides"


def _rec(fp: str, token_fp: str = ""):
    from corvin_console.auth import SessionRecord

    now = time.time()
    return SessionRecord(
        sid="raw-" + fp, sid_fingerprint=fp, tier="operator", tenant_id="_default",
        token_fingerprint=token_fp, csrf_secret="s", csrf_nonce="n" * 32,
        csrf_nonce_issued_at=now, created_at=now, last_seen_at=now,
        expires_at=now + 60,
    )


def test_two_sessions_two_identities():
    from corvin_console.routes.control_plane_overrides import _approver_id

    a, b = _approver_id(_rec("fp-a")), _approver_id(_rec("fp-b"))
    assert a != b
    assert "raw-" not in a and "raw-" not in b  # never the session cookie


def test_empty_fingerprint_never_yields_shared_identity():
    from fastapi import HTTPException
    from corvin_console.routes.control_plane_overrides import _approver_id

    for fp in ("", "   "):
        with pytest.raises(HTTPException) as exc:
            _approver_id(_rec(fp, token_fp=""))
        assert exc.value.status_code == 403


def _grant(home: Path, sid: str) -> None:
    from core.compliance import consent_store
    from corvin_console import auth as _auth

    from core.compliance.consent import consent_subject

    consent_store.get_consent_store("_default", corvin_home=home).grant_consent(
        user_id=consent_subject(_auth.load_session(sid)),
        scope="control_plane_override_operations")


def test_approver_set_is_per_session_and_four_eyes_over_http():
    with _sandbox(Path(tempfile.mkdtemp())) as (client_a, csrf_a, home, _):
        from fastapi.testclient import TestClient
        from core.compliance import consent_store
        from corvin_console import auth as _auth
        from corvin_console.routes import control_plane_overrides as ov

        consent_store._stores.clear()
        ov._authority = None
        sid_a = client_a.cookies.get("corvin_console_sid")
        rec_b = _auth.create_session(tenant_id="_default", token_fingerprint="")
        client_b = TestClient(client_a.app, raise_server_exceptions=False)
        client_b.cookies.set("corvin_console_sid", rec_b.sid)
        csrf_b = _auth.derive_csrf_token(rec_b.csrf_secret, rec_b.sid)
        _grant(home, sid_a)
        _grant(home, rec_b.sid)

        r = client_a.post(BASE, headers={"X-CSRF-Token": csrf_a},
                          json={"override_type": "force_enable", "target_id": "plugin-x",
                                "reason": "maintenance window"})
        assert r.status_code == 200, r.text
        oid = r.json()["override_id"]

        # Register ONLY session A as approver. Under the old "token:" identity
        # that made session B an approver too.
        authority = ov.get_authority()
        authority.add_approver(ov._approver_id(_auth.load_session(sid_a)))

        def approve(client, csrf):
            return client.post(f"{BASE}/{oid}/approve",
                               headers={"X-CSRF-Token": csrf}, json={"reason": "ok"})

        assert approve(client_b, csrf_b).status_code == 403  # not an approver
        assert approve(client_a, csrf_a).status_code == 403  # own request

        authority.add_approver(ov._approver_id(_auth.load_session(rec_b.sid)))
        r = approve(client_b, csrf_b)
        assert r.status_code == 200, r.text
        ov._authority = None
