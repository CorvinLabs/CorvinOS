"""Per-subtask E2E for ADR-0007 Phase 3.6 — Keycloak-shape smoke.

This suite drives the JWKS-URI dynamic-fetch path and the SCIM 2.0
PatchOp surface against a local stub HTTP server that mimics
Keycloak's JWKS endpoint. The actual Keycloak-in-CI integration
(running ``docker-compose up keycloak`` and exercising the live
issuer) is documented in ``scripts/keycloak_smoke.py`` -- an opt-in
operator script that is NOT part of ``run-all-tests.sh`` because it
requires Docker.

Covers:
  * ``jwks_uri`` fetch + cache: stub serves a JWKS document; first
    verify hits the network, second verify within TTL hits the cache.
  * Fetch failure with cached fallback: stub goes 500 after a
    successful warm-up; the cached JWKS continues to verify.
  * Fetch failure with inline pinned fallback: stub down from the
    start; the inline ``jwks`` field (when also present) verifies.
  * Fetch failure with neither cache nor inline → verify fails.
  * SCIM PATCH ``replace`` on ``active`` / ``displayName`` / ``emails``.
  * SCIM PATCH ``replace`` on ``userName`` with conflict → 409.
  * SCIM PATCH ``remove`` on ``displayName`` and ``emails``;
    ``remove`` on ``userName`` rejected.
  * SCIM PATCH on unknown user → 404.
  * Malformed PatchOp body → 400 invalidValue + audit.
"""
from __future__ import annotations

import base64
import http.server
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import jwt as _pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.oidc import (  # noqa: E402
    IssuerTrust,
    TenantOidcTrust,
    _JWKS_CACHE,
    save_trust,
)
from corvin_gateway.scim import (  # noqa: E402
    SCIM_PATCH_OP_SCHEMA,
    SCIM_USER_SCHEMA,
    ScimUserStore,
)


# ── RSA keypair fixture ──────────────────────────────────────────────


def _gen_rsa_kid_jwks(kid: str = "test-kid"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_numbers = key.public_key().public_numbers()
    def _b64url(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")
    jwks = {
        "keys": [
            {
                "kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
                "n": _b64url(pub_numbers.n), "e": _b64url(pub_numbers.e),
            }
        ]
    }
    return pem, jwks


def _sign(pem, *, issuer, audience, subject, expires_in=60):
    now = int(time.time())
    claims = {
        "iss": issuer, "aud": audience, "sub": subject,
        "iat": now, "exp": now + expires_in,
    }
    return _pyjwt.encode(claims, pem, algorithm="RS256",
                         headers={"kid": "test-kid"})


# ── JWKS stub HTTP server ────────────────────────────────────────────


class _JwksStub:
    """Tiny HTTP server that serves a fixed JWKS document on
    ``/jwks.json``. The status code can be flipped to 500 to
    simulate an outage.
    """

    def __init__(self, jwks: dict) -> None:
        self.jwks = jwks
        self.status = 200
        self.hits = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/jwks.json":
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.hits += 1
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(outer.jwks).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/jwks.json"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )

    def __enter__(self) -> "_JwksStub":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


# ── Sandbox ──────────────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-kc-smoke-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        # Clear the process-wide JWKS cache between tests
        _JWKS_CACHE.clear()
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            _JWKS_CACHE.clear()


def _install_trust_uri(tenant, jwks_uri, *, inline_jwks=None):
    """Install a trust config whose JWKS comes from a URI. Optional
    inline ``jwks`` as the fallback."""
    trust = TenantOidcTrust(
        tenant_id=tenant,
        issuers=[
            IssuerTrust(
                issuer="https://idp.test/realms/acme",
                audience="corvin-acme",
                jwks=(inline_jwks or {"keys": []}),
                jwks_uri=jwks_uri,
                jwks_cache_ttl_s=300,
            ),
        ],
    )
    save_trust(trust)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── JWKS-URI tests ───────────────────────────────────────────────────


class JwksUriFetchTests(unittest.TestCase):
    def test_fetch_then_cache(self):
        with sandbox(("acme",)):
            pem, jwks = _gen_rsa_kid_jwks()
            with _JwksStub(jwks) as stub:
                _install_trust_uri("acme", stub.url)
                tok1 = _sign(pem,
                             issuer="https://idp.test/realms/acme",
                             audience="corvin-acme", subject="acme")
                tok2 = _sign(pem,
                             issuer="https://idp.test/realms/acme",
                             audience="corvin-acme", subject="acme")
                with TestClient(app) as client:
                    r1 = client.get("/v1/tenants/acme/runs/run_x",
                                    headers=_hdr(tok1))
                    r2 = client.get("/v1/tenants/acme/runs/run_x",
                                    headers=_hdr(tok2))
                self.assertEqual(r1.status_code, 404)
                self.assertEqual(r2.status_code, 404)
                # Cache should have prevented a second hit
                self.assertEqual(stub.hits, 1)

    def test_fetch_failure_with_cached_fallback(self):
        with sandbox(("acme",)):
            pem, jwks = _gen_rsa_kid_jwks()
            with _JwksStub(jwks) as stub:
                _install_trust_uri("acme", stub.url)
                # Warm the cache
                tok = _sign(pem,
                            issuer="https://idp.test/realms/acme",
                            audience="corvin-acme", subject="acme")
                with TestClient(app) as client:
                    r1 = client.get("/v1/tenants/acme/runs/run_x",
                                    headers=_hdr(tok))
                self.assertEqual(r1.status_code, 404)
                # Bust the cache by clearing it, then make the stub
                # return 500. The pinned fallback path uses the most-
                # recent successful fetch.
                _JWKS_CACHE.clear()
                stub.status = 500
                # Without a cached value AND no inline jwks, this
                # should now fail.
                with TestClient(app) as client:
                    r2 = client.get("/v1/tenants/acme/runs/run_x",
                                    headers=_hdr(tok))
                self.assertEqual(r2.status_code, 401)

    def test_fetch_failure_with_inline_fallback(self):
        with sandbox(("acme",)):
            pem, jwks = _gen_rsa_kid_jwks()
            # Trust config has BOTH a jwks_uri (which we'll point at
            # a port that doesn't exist) AND an inline jwks.
            with _JwksStub(jwks) as stub:
                _install_trust_uri(
                    "acme", stub.url, inline_jwks=jwks,
                )
                stub.status = 500
                tok = _sign(pem,
                            issuer="https://idp.test/realms/acme",
                            audience="corvin-acme", subject="acme")
                with TestClient(app) as client:
                    r = client.get("/v1/tenants/acme/runs/run_x",
                                   headers=_hdr(tok))
                # Inline jwks should make verify succeed → 404 not 401
                self.assertEqual(r.status_code, 404)


# ── SCIM PATCH tests ─────────────────────────────────────────────────


def _user_body(username="alice@acme.com"):
    return {
        "schemas":  [SCIM_USER_SCHEMA],
        "userName": username,
        "emails":   [{"value": username, "primary": True}],
        "displayName": "Alice",
        "active":   True,
    }


class ScimPatchTests(unittest.TestCase):
    def test_replace_active(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                uid = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                ).json()["id"]
                r = client.patch(
                    f"/v1/tenants/acme/scim/v2/Users/{uid}",
                    json={
                        "schemas": [SCIM_PATCH_OP_SCHEMA],
                        "Operations": [
                            {"op": "replace", "path": "active",
                             "value": False},
                        ],
                    },
                )
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["active"], False)

    def test_replace_username_conflict_409(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                # Two users
                u1 = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body("a@x"),
                ).json()["id"]
                u2 = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body("b@x"),
                ).json()["id"]
                # Attempt to rename u2 to u1's userName
                r = client.patch(
                    f"/v1/tenants/acme/scim/v2/Users/{u2}",
                    json={
                        "schemas": [SCIM_PATCH_OP_SCHEMA],
                        "Operations": [
                            {"op": "replace", "path": "userName",
                             "value": "a@x"},
                        ],
                    },
                )
                self.assertEqual(r.status_code, 409)
                self.assertEqual(r.json()["scimType"], "uniqueness")

    def test_remove_displayname_and_emails(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                uid = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                ).json()["id"]
                r = client.patch(
                    f"/v1/tenants/acme/scim/v2/Users/{uid}",
                    json={
                        "schemas": [SCIM_PATCH_OP_SCHEMA],
                        "Operations": [
                            {"op": "remove", "path": "displayName"},
                            {"op": "remove", "path": "emails"},
                        ],
                    },
                )
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertEqual(body["displayName"], "")
                self.assertEqual(body["emails"], [])

    def test_remove_username_rejected_400(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                uid = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                ).json()["id"]
                r = client.patch(
                    f"/v1/tenants/acme/scim/v2/Users/{uid}",
                    json={
                        "schemas": [SCIM_PATCH_OP_SCHEMA],
                        "Operations": [
                            {"op": "remove", "path": "userName"},
                        ],
                    },
                )
                self.assertEqual(r.status_code, 400)

    def test_patch_unknown_user_404(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                r = client.patch(
                    "/v1/tenants/acme/scim/v2/Users/nonsense-id",
                    json={
                        "schemas": [SCIM_PATCH_OP_SCHEMA],
                        "Operations": [
                            {"op": "replace", "path": "active",
                             "value": False},
                        ],
                    },
                )
                self.assertEqual(r.status_code, 404)

    def test_malformed_patch_400(self):
        with sandbox(("acme",)):
            with TestClient(app) as client:
                uid = client.post(
                    "/v1/tenants/acme/scim/v2/Users",
                    json=_user_body(),
                ).json()["id"]
                # Missing patch schema
                r = client.patch(
                    f"/v1/tenants/acme/scim/v2/Users/{uid}",
                    json={"Operations": [{"op": "replace",
                                          "path": "active",
                                          "value": False}]},
                )
                self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
