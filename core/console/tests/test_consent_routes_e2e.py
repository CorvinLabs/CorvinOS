"""HTTP E2E proof for the console consent self-service routes (2026-09-27).

Before these routes existed nothing on the live service could grant the
``learning_feedback`` or ``control_plane_override_operations`` scopes, so the
learning feedback route and every control-plane override route answered 403
for everyone. Driven through the REAL console router (``TestClient``):

* grant → the consent-gated route is accepted → revoke → 403 again;
* the subject is the session FINGERPRINT — the raw session id is never
  returned, stored or audited;
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


if __name__ == "__main__":
    unittest.main()
