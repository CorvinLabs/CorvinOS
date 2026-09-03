"""``_jwt_guard`` peer + tenant binding (adversarial review E-09, 2026-09-03).

* A NON-loopback peer without a Bearer JWT is 401 on every tenant route
  (SCIM, runs, metrics) — before the route runs.
* A loopback peer without a token is the local operator (route runs).
* A valid JWT for tenant A presented on ``/v1/tenants/B/...`` is 403.
* ``audit_metrics._cache`` is a bounded LRU.

Runs the real ``corvin_gateway.app`` through ``TestClient``; the remote peer
is set explicitly (the suite conftest defaults every client to loopback).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corvin_gateway.app import app  # noqa: E402
from corvin_gateway import audit_metrics  # noqa: E402

from .test_oidc import _gen_rsa_kid_jwks, _hdr, _install_trust, _sign_jwt, sandbox  # noqa: E402

REMOTE = ("203.0.113.9", 4242)


class NonLoopbackPeerRequiresJwt(unittest.TestCase):
    def test_scim_runs_metrics_are_401_without_bearer(self):
        with sandbox(("acme",)):
            with TestClient(app, client=REMOTE) as c:
                for path in (
                    "/v1/tenants/acme/scim/v2/Users",
                    "/v1/tenants/acme/runs/nope",
                    "/v1/tenants/acme/metrics",
                ):
                    r = c.get(path)
                    self.assertEqual(r.status_code, 401, path)
                    self.assertEqual(
                        r.json()["detail"]["reason"], "bearer-required-for-non-loopback-peer"
                    )
                r = c.post("/v1/tenants/acme/scim/v2/Users", json={"userName": "x"})
                self.assertEqual(r.status_code, 401)

    def test_unparseable_peer_is_treated_as_remote(self):
        with sandbox(("acme",)):
            with TestClient(app, client=("testclient", 50000)) as c:
                self.assertEqual(c.get("/v1/tenants/acme/runs/nope").status_code, 401)

    def test_loopback_peer_without_bearer_reaches_the_route(self):
        with sandbox(("acme",)):
            for peer in (("127.0.0.1", 1), ("::1", 1), ("127.9.9.9", 1)):
                with TestClient(app, client=peer) as c:
                    r = c.get("/v1/tenants/acme/runs/nope")
                    self.assertEqual(r.status_code, 404, peer)  # run-not-found, not 401

    def test_remote_peer_with_valid_jwt_passes(self):
        with sandbox(("acme",)):
            pem, jwks = _gen_rsa_kid_jwks()
            _install_trust("acme", jwks)
            tok = _sign_jwt(pem, issuer="https://idp.example/realms/acme",
                            audience="corvin-acme", subject="acme")
            with TestClient(app, client=REMOTE) as c:
                r = c.get("/v1/tenants/acme/runs/nope", headers=_hdr(tok))
            self.assertEqual(r.status_code, 404)


class PathTenantBoundToPrincipal(unittest.TestCase):
    def test_jwt_for_tenant_a_cannot_address_tenant_b(self):
        with sandbox(("acme", "beta")):
            pem, jwks = _gen_rsa_kid_jwks()
            _install_trust("acme", jwks)
            tok = _sign_jwt(pem, issuer="https://idp.example/realms/acme",
                            audience="corvin-acme", subject="acme")
            with TestClient(app, client=REMOTE) as c:
                r = c.get("/v1/tenants/beta/runs/nope", headers=_hdr(tok))
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.json()["detail"]["reason"], "tenant-mismatch")
                r = c.get("/v1/tenants/beta/scim/v2/Users", headers=_hdr(tok))
                self.assertEqual(r.status_code, 403)
                # and the matching tenant still works
                self.assertEqual(
                    c.get("/v1/tenants/acme/runs/nope", headers=_hdr(tok)).status_code, 404,
                )


class MetricsCacheIsBounded(unittest.TestCase):
    def test_lru_evicts_oldest(self):
        with sandbox(("acme",)):
            audit_metrics.clear_cache()
            n = audit_metrics._CACHE_MAX + 40
            for i in range(n):
                audit_metrics.render("acme", since=float(1_000_000 + i))
            self.assertLessEqual(len(audit_metrics._cache), audit_metrics._CACHE_MAX)
            self.assertNotIn(("acme", 1_000_000), audit_metrics._cache)
            self.assertIn(("acme", 1_000_000 + n - 1), audit_metrics._cache)
            audit_metrics.clear_cache()


if __name__ == "__main__":
    unittest.main(verbosity=2)
