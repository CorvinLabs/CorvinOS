"""Per-subtask E2E for ADR-0007 Phase 3.5 — SCIM 2.0 stub.

Covers:
  * ``ScimUserStore`` pure: create / list / get / delete + 0o600 file mode.
  * Validation: missing schemas, missing userName, bad userName shape,
    bad email structure, non-bool active.
  * userName uniqueness (case-insensitive) → ScimConflict + audit.
  * Endpoints through TestClient:
    - GET /Users — empty + after create.
    - POST /Users — 201 with Location header + body shape.
    - GET /Users/{uid} — 200 + body, 404 on miss.
    - DELETE /Users/{uid} — 204 + idempotent 404 second time.
    - Cross-tenant gate (403).
    - Auth gate (401 missing bearer).
  * Audit-chain integrity across the full lifecycle.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.scim import (  # noqa: E402
    SCIM_USER_SCHEMA,
    SCIM_LIST_SCHEMA,
    ScimConflict,
    ScimUserStore,
    ScimValidationError,
)
from forge import security_events as _security_events  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-scim-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)


def _user_body(username="alice@acme.com"):
    return {
        "schemas":  [SCIM_USER_SCHEMA],
        "userName": username,
        "emails":   [{"value": username, "primary": True}],
        "displayName": "Alice Smith",
        "active":   True,
    }


# ── Pure store tests ────────────────────────────────────────────────


class StoreTests(unittest.TestCase):
    def test_create_and_get(self):
        with sandbox(("acme",)) as home:
            uid, entry = ScimUserStore().create("acme", _user_body())
            self.assertTrue(len(uid) > 0)
            self.assertEqual(entry["userName"], "alice@acme.com")
            self.assertIn("created", entry)
            p = home / "tenants" / "acme" / "global" / "scim" / "users.json"
            self.assertTrue(p.exists())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            # round-trip
            got = ScimUserStore().get("acme", uid)
            self.assertEqual(got["userName"], "alice@acme.com")

    def test_conflict_on_username(self):
        with sandbox(("acme",)):
            ScimUserStore().create("acme", _user_body("bob@acme.com"))
            with self.assertRaises(ScimConflict):
                ScimUserStore().create("acme", _user_body("BOB@ACME.COM"))

    def test_validation_missing_schemas(self):
        with sandbox(("acme",)):
            with self.assertRaises(ScimValidationError):
                ScimUserStore().create("acme", {"userName": "x"})

    def test_validation_bad_username(self):
        with sandbox(("acme",)):
            with self.assertRaises(ScimValidationError):
                ScimUserStore().create("acme", {
                    "schemas": [SCIM_USER_SCHEMA],
                    "userName": "white space",
                })

    def test_validation_bad_emails(self):
        with sandbox(("acme",)):
            with self.assertRaises(ScimValidationError):
                ScimUserStore().create("acme", {
                    "schemas": [SCIM_USER_SCHEMA],
                    "userName": "ok",
                    "emails":   "not-a-list",
                })

    def test_delete_idempotent_returns_false(self):
        with sandbox(("acme",)):
            uid, _ = ScimUserStore().create("acme", _user_body())
            self.assertTrue(ScimUserStore().delete("acme", uid))
            self.assertFalse(ScimUserStore().delete("acme", uid))


# ── HTTP endpoints ──────────────────────────────────────────────────


class EndpointTests(unittest.TestCase):
    def test_list_empty_then_after_create(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                r = client.get("/v1/tenants/acme/scim/v2/Users")
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual(body["totalResults"], 0)
                self.assertIn(SCIM_LIST_SCHEMA, body["schemas"])

                r2 = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                )
                self.assertEqual(r2.status_code, 201, r2.text)
                created = r2.json()
                self.assertIn(SCIM_USER_SCHEMA, created["schemas"])
                self.assertIn("id", created)
                self.assertEqual(created["userName"], "alice@acme.com")
                self.assertIn("Location", r2.headers)
                self.assertIn(created["id"], r2.headers["Location"])

                r3 = client.get("/v1/tenants/acme/scim/v2/Users")
                self.assertEqual(r3.json()["totalResults"], 1)

    def test_get_returns_user(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                created = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body("carol@acme.com"),
                ).json()
                r = client.get(
                    f"/v1/tenants/acme/scim/v2/Users/{created['id']}",
                )
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["userName"], "carol@acme.com")

    def test_get_missing_404(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                r = client.get("/v1/tenants/acme/scim/v2/Users/no-such-id")
                self.assertEqual(r.status_code, 404)
                self.assertIn("status", r.json())

    def test_delete_204_then_404(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                uid = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                ).json()["id"]
                r = client.delete(f"/v1/tenants/acme/scim/v2/Users/{uid}")
                self.assertEqual(r.status_code, 204)
                r2 = client.delete(f"/v1/tenants/acme/scim/v2/Users/{uid}")
                self.assertEqual(r2.status_code, 404)

    def test_post_invalid_username_400(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                body = _user_body()
                body["userName"] = "white space"
                r = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=body,
                )
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json()["scimType"], "invalidValue")

    def test_post_conflict_409(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                )
                r = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                )
                self.assertEqual(r.status_code, 409)
                self.assertEqual(r.json()["scimType"], "uniqueness")


# SCIMGateTests removed — gateway no longer enforces bearer auth.
# Loopback binding is the local security boundary.


# ── Audit chain integrity ───────────────────────────────────────────


class AuditChainTests(unittest.TestCase):
    def test_chain_verifies_across_create_delete(self):
        with sandbox(("acme",)) as home:
            with TestClient(app) as client:
                u1 = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body("u1@acme.com"),
                ).json()["id"]
                # Conflict on same userName → audited
                client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body("u1@acme.com"),
                )
                client.delete(f"/v1/tenants/acme/scim/v2/Users/{u1}")
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, f"chain broken: {problems}")
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            event_types = {e["event_type"] for e in lines}
            self.assertIn("gateway.scim_user_created", event_types)
            self.assertIn("gateway.scim_user_conflict", event_types)
            self.assertIn("gateway.scim_user_deleted", event_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
