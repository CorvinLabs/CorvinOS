"""HTTP E2E proof for the console consent self-service routes (2026-09-27).

Before these routes existed nothing on the live service could grant the
``learning_feedback`` or ``control_plane_override_operations`` scopes, so the
learning feedback route and every control-plane override route answered 403
for everyone. Driven through the REAL console router (``TestClient``):

* grant → the consent-gated route is accepted → revoke → 403 again;
* the subject is derived from the session record (the local operator's
  principal, or the session fingerprint for a credential session) — the raw
  session id is never returned, stored or audited;
* a consent given in one login is withdrawable from any other login of the
  same operator (GDPR Art. 7(3), ``ConsentWithdrawalAcrossLogins``);
* CSRF is required, unknown scopes are 404, TTL is clamped to the cap;
* the grant is audited on the tenant's one chain BEFORE it takes effect.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_learning_loop_routes_e2e import _sandbox as _learning_sandbox  # noqa: E402
from test_admin_route import _sandbox as _admin_sandbox  # noqa: E402

FEEDBACK = {"task_id": "task-1", "outcome_quality": "good", "would_repeat": True}


def _chain_records(home: Path) -> list[dict]:
    out: list[dict] = []
    for f in home.rglob("audit.jsonl"):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


class ConsentRoutesE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        from core.compliance import consent_store
        consent_store._stores.clear()

    def test_grant_feedback_accepted_revoke_403(self):
        with _learning_sandbox(Path(self._tmp), grant_feedback_consent=False) as (
            client, home, tenant_id, _emitter, _chain,
        ):
            sid = client.cookies.get("corvin_console_sid")
            csrf = client.headers["X-CSRF-Token"]

            # deny-by-default
            r = client.post("/v1/console/learning/feedback", json=FEEDBACK)
            self.assertEqual(r.status_code, 403, r.text)

            r = client.get("/v1/console/consent")
            self.assertEqual(r.status_code, 200, r.text)
            state = {s["scope"]: s["active"] for s in r.json()["scopes"]}
            self.assertFalse(state["learning_feedback"])
            self.assertNotIn(sid, r.text)

            r = client.post("/v1/console/consent/learning_feedback", json={"ttl_days": 7})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["active"])
            self.assertTrue(r.json()["audit_ref"])
            self.assertNotIn(sid, r.text)

            r = client.post("/v1/console/learning/feedback", json=FEEDBACK)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "recorded")

            r = client.delete("/v1/console/consent/learning_feedback")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"scope": "learning_feedback", "active": False,
                                        "was_active": True, "audited": True})

            r = client.post("/v1/console/learning/feedback", json=FEEDBACK)
            self.assertEqual(r.status_code, 403, r.text)

            # the raw session id is nowhere at rest
            for db in home.rglob("consent_store.db"):
                rows = sqlite3.connect(db).execute("SELECT user_id FROM consent_records").fetchall()
                self.assertTrue(rows)
                self.assertNotIn(sid, [u for (u,) in rows])
            records = _chain_records(home)
            self.assertNotIn(sid, json.dumps(records))
            types = [c.get("event_type") for c in records]
            self.assertEqual(types.count("console.consent_granted"), 1)
            self.assertEqual(types.count("console.consent_revoked"), 1)
            granted = next(c for c in records if c.get("event_type") == "console.consent_granted")
            self.assertEqual(granted["details"]["consent_scope"], "learning_feedback")
            self.assertEqual(granted["details"]["ttl_s"], 7 * 86400)
            self.assertTrue(granted["details"]["sid_fingerprint"])
            self.assertEqual(csrf, client.headers["X-CSRF-Token"])

    def test_guards_csrf_scope_and_ttl_cap(self):
        with _learning_sandbox(Path(self._tmp), grant_feedback_consent=False) as (
            client, _home, _tenant_id, _emitter, _chain,
        ):
            csrf = client.headers.pop("X-CSRF-Token")
            r = client.post("/v1/console/consent/learning_feedback")
            self.assertEqual(r.status_code, 403, r.text)       # CSRF required
            client.headers["X-CSRF-Token"] = csrf

            self.assertEqual(client.post("/v1/console/consent/default").status_code, 404)
            self.assertEqual(client.post("/v1/console/consent/../../x").status_code, 404)
            self.assertEqual(client.post("/v1/console/consent/learning_feedback",
                                         json={"ttl_days": 0}).status_code, 400)
            self.assertEqual(client.post("/v1/console/consent/learning_feedback",
                                         json={"ttl_days": 1, "user_id": "x"}).status_code, 422)

            r = client.post("/v1/console/consent/learning_feedback", json={"ttl_days": 100000})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            window = datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(body["granted_at"])
            from core.compliance.consent_store import MAX_TTL_DAYS
            self.assertLessEqual(window.total_seconds(), MAX_TTL_DAYS * 86400)

            client.cookies.clear()
            self.assertEqual(client.get("/v1/console/consent").status_code, 401)

    def test_audit_failure_grants_nothing(self):
        with _learning_sandbox(Path(self._tmp), grant_feedback_consent=False) as (
            client, _home, _tenant_id, _emitter, _chain,
        ):
            from corvin_console.routes import consent as consent_route

            orig = consent_route._audit
            consent_route._audit = lambda *a, **k: None
            try:
                r = client.post("/v1/console/consent/learning_feedback")
            finally:
                consent_route._audit = orig
            self.assertEqual(r.status_code, 503, r.text)
            r = client.post("/v1/console/learning/feedback", json=FEEDBACK)
            self.assertEqual(r.status_code, 403, r.text)

    def test_control_plane_override_scope_grant_revoke(self):
        with _admin_sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            base = "/v1/console/control-plane/overrides"
            self.assertEqual(client.get(base).status_code, 403)
            h = {"X-CSRF-Token": csrf}
            r = client.post("/v1/console/consent/control_plane_override_operations", headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(client.get(base).status_code, 200)
            r = client.delete("/v1/console/consent/control_plane_override_operations", headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(client.get(base).status_code, 403)


class ConsentWithdrawalAcrossLogins(unittest.TestCase):
    """GDPR Art. 7(3): withdrawal must be as easy as giving consent.

    Until 2026-09-28 the subject was the SESSION fingerprint. A consent given
    in login A could not be withdrawn from login B (B's DELETE answered 200
    ``was_active: false`` while A kept using the grant), logout did not end
    it, and every new login started at 403. Both logins here go through the
    REAL ``/auth/local-login`` route — the only way the console mints a
    session — from a loopback peer.
    """

    def setUp(self):
        from core.compliance import consent_store
        consent_store._stores.clear()

    def _login(self, app, auth):
        from fastapi.testclient import TestClient

        c = TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000),
                       follow_redirects=False)
        r = c.get("/v1/console/auth/local-login")
        self.assertEqual(r.status_code, 302, r.text)
        sid = c.cookies.get("corvin_console_sid")
        self.assertTrue(sid)
        rec = auth.load_session(sid)
        c.headers["X-CSRF-Token"] = auth.derive_csrf_token(rec.csrf_secret, sid)
        return c, sid, rec

    def test_withdrawal_from_login_b_ends_consent_used_by_login_a(self):
        with _learning_sandbox(Path(tempfile.mkdtemp()), grant_feedback_consent=False) as (
            client, home, tenant_id, _emitter, _chain,
        ):
            from corvin_console import auth as _auth

            app = client.app
            a, sid_a, rec_a = self._login(app, _auth)
            b, sid_b, rec_b = self._login(app, _auth)
            self.assertNotEqual(rec_a.sid_fingerprint, rec_b.sid_fingerprint)

            # deny-by-default in both logins
            self.assertEqual(a.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)
            self.assertEqual(b.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)

            r = a.post("/v1/console/consent/learning_feedback")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(a.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 200)
            # the same operator in another login sees and uses the same consent
            listed = {s["scope"]: s["active"] for s in b.get("/v1/console/consent").json()["scopes"]}
            self.assertTrue(listed["learning_feedback"])
            self.assertEqual(b.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 200)

            # B withdraws → the withdrawal is effective for A too
            r = b.delete("/v1/console/consent/learning_feedback")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["was_active"])
            self.assertTrue(r.json()["audited"])
            self.assertEqual(a.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)
            self.assertEqual(b.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)

            # no raw session id at rest: consent DB, audit chain, list response
            for db in home.rglob("consent_store.db"):
                subjects = [u for (u,) in sqlite3.connect(db).execute(
                    "SELECT user_id FROM consent_records").fetchall()]
                self.assertEqual(subjects, [f"local-operator:{tenant_id}"])
            dump = json.dumps(_chain_records(home)) + b.get("/v1/console/consent").text
            for sid in (sid_a, sid_b):
                self.assertNotIn(sid, dump)
            revoked = [c for c in _chain_records(home)
                       if c.get("event_type") == "console.consent_revoked"]
            self.assertEqual(len(revoked), 1)
            self.assertEqual(revoked[0]["details"]["consent_subject"], f"local-operator:{tenant_id}")
            self.assertEqual(revoked[0]["details"]["sid_fingerprint"], rec_b.sid_fingerprint)

    def test_internal_tool_cannot_grant_or_withdraw_but_is_gated(self):
        """The model's corvin-browser record maps to the same operator for the
        CHECK (it acts for them) but can never grant or withdraw."""
        from core.compliance.consent import consent_subject
        from corvin_console import auth as _auth
        import time as _t

        now = _t.time()
        tool = _auth.SessionRecord(
            sid="mcp:abc", sid_fingerprint="0123456789ab", tier="owner", tenant_id="_default",
            token_fingerprint="", csrf_secret="", csrf_nonce="", csrf_nonce_issued_at=now,
            created_at=now, last_seen_at=now, expires_at=now + 60, is_internal_tool=True,
        )
        self.assertEqual(consent_subject(tool), "local-operator:_default")
        from corvin_console.routes import consent as consent_route
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            consent_route._human_only(tool)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_subject_identity_rules(self):
        """Self-only, no raw sid, fail-closed on missing identity."""
        from types import SimpleNamespace as NS
        from core.compliance.consent import ConsentError, consent_subject

        local = NS(sid="S" * 43, sid_fingerprint="aaaaaaaaaaaa", tier="owner",
                   tenant_id="_default", token_fingerprint="")
        self.assertEqual(consent_subject(local), "local-operator:_default")
        # a session carrying a credential identity stays per-session (a future
        # multi-user login kind must never share one person's consent)
        cred = NS(sid="S" * 43, sid_fingerprint="bbbbbbbbbbbb", tier="owner",
                  tenant_id="_default", token_fingerprint="tok-fp")
        self.assertEqual(consent_subject(cred), "bbbbbbbbbbbb")
        for bad in (
            NS(sid="S" * 43, sid_fingerprint="aaaaaaaaaaaa", tier="owner", tenant_id="", token_fingerprint=""),
            NS(sid="S" * 43, sid_fingerprint="aaaaaaaaaaaa", tier="owner", tenant_id="../x", token_fingerprint=""),
            NS(sid="S" * 43, sid_fingerprint="", tier="owner", tenant_id="_default", token_fingerprint="t"),
            NS(sid="S" * 43, sid_fingerprint="aaaaaaaaaaaa", tier="viewer", tenant_id="_default", token_fingerprint=""),
        ):
            with self.assertRaises(ConsentError):
                consent_subject(bad)

    def test_session_tenant_other_than_process_tenant_fails_closed(self):
        """Documented limitation (ADR-2093): one tenant per process. The audit
        chokepoint refuses a record tagged with a non-process tenant, so the
        grant is 503 and NOTHING is granted; a withdrawal still lands."""
        import os
        with _learning_sandbox(Path(tempfile.mkdtemp()), grant_feedback_consent=False) as (
            client, _home, tenant_id, _emitter, _chain,
        ):
            from corvin_console import auth as _auth
            from core.compliance.consent_store import get_consent_store

            a, _sid, _rec = self._login(client.app, _auth)
            prev = os.environ["CORVIN_TENANT_ID"]
            os.environ["CORVIN_TENANT_ID"] = "other-tenant"
            try:
                r = a.post("/v1/console/consent/learning_feedback")
                self.assertEqual(r.status_code, 503, r.text)
                self.assertFalse(get_consent_store(tenant_id).get_consent(
                    user_id=f"local-operator:{tenant_id}", scope="learning_feedback"))
                get_consent_store(tenant_id).grant_consent(
                    user_id=f"local-operator:{tenant_id}", scope="learning_feedback")
                r = a.delete("/v1/console/consent/learning_feedback")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["was_active"], True)
                self.assertEqual(r.json()["audited"], False)
            finally:
                os.environ["CORVIN_TENANT_ID"] = prev
            self.assertEqual(a.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)

    def test_withdrawal_also_ends_legacy_per_session_grants(self):
        """Grants written under the old per-session subject are no longer
        consulted — and the operator's withdrawal ends them too, so no
        un-withdrawable consent row is left behind."""
        with _learning_sandbox(Path(tempfile.mkdtemp()), grant_feedback_consent=False) as (
            client, home, tenant_id, _emitter, _chain,
        ):
            from corvin_console import auth as _auth
            from core.compliance.consent_store import get_consent_store

            a, _sid, rec_a = self._login(client.app, _auth)
            store = get_consent_store(tenant_id)
            store.grant_consent(user_id=rec_a.sid_fingerprint, scope="learning_feedback")
            # a legacy per-session row does NOT satisfy the operator gate
            self.assertEqual(a.post("/v1/console/learning/feedback", json=FEEDBACK).status_code, 403)
            r = a.delete("/v1/console/consent/learning_feedback")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["was_active"])
            self.assertFalse(store.get_consent(user_id=rec_a.sid_fingerprint, scope="learning_feedback"))


if __name__ == "__main__":
    unittest.main()
