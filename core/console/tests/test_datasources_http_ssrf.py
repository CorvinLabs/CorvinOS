"""Regression tests for the DSI-v2 HTTP-adapter SSRF + credential-exfiltration
hardening (CON-DS-V2-02).

Threat model recap (all attacks assume an *authenticated, paid-tier* console
user, since ``ping`` is license-gated):

  * SSRF — ``base_url`` pointed at ``169.254.169.254`` / ``localhost`` / a
    private IP (directly or via a hostname that resolves there) makes the server
    fetch internal / cloud-metadata endpoints and reflect the response.
  * Secret exfiltration — ``auth_type=bearer`` + ``auth_env=<ANY env var>``
    makes the server read ``os.environ[auth_env]`` and ship it as a bearer token
    to an attacker-controlled host, leaking any process secret.

The guard must FAIL CLOSED: reject non-http(s) schemes, private/loopback/
link-local/reserved/metadata targets, hostnames resolving to non-public
addresses, redirects to such hosts, and arbitrary ``auth_env`` names — while a
legitimate public https adapter with an allowlisted credential var still works.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

# ── Path bootstrap (mirror test_license_http_gates.py) ─────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_CONSOLE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    """Sandboxed console app with a live authenticated session + CSRF."""
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home, tenant_id, csrf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _unlimited_license():
    """Patch the tier gate so registration is not blocked (attacker is paid-tier)."""
    return patch(
        "corvin_console.routes.datasources_http._lic_get_limit",
        return_value=None,
    )


def _addrinfo(ip: str):
    fam = 10 if ":" in ip else 2  # AF_INET6 / AF_INET
    return [(fam, 1, 6, "", (ip, 0))]


# ── Unit tests on the guard primitives (no network, no app) ────────────────────

class TestSsrfGuardPrimitives(unittest.TestCase):
    def _mod(self):
        import importlib
        return importlib.import_module("corvin_console.routes.datasources_http")

    def test_non_http_scheme_rejected(self):
        m = self._mod()
        for url in ("file:///etc/passwd", "gopher://x/", "ftp://h/", "//h/x", "ws://h/"):
            with self.assertRaises(m._UnsafeUrl, msg=url):
                m._assert_scheme_and_static_host(url)

    def test_literal_private_and_metadata_ips_rejected(self):
        m = self._mod()
        for url in (
            "http://169.254.169.254/latest/meta-data/",   # AWS/GCP/Azure IMDS
            "http://127.0.0.1:9000/",                     # loopback
            "http://[::1]/",                              # IPv6 loopback
            "http://10.0.0.5/",                           # RFC-1918
            "http://192.168.1.1/",                        # RFC-1918
            "http://0.0.0.0/",                            # unspecified → loopback route
            "http://2130706433/",                         # decimal 127.0.0.1
            "http://0x7f000001/",                         # hex 127.0.0.1
            "http://192.0.0.192/",                        # Oracle Cloud IMDS
            "http://100.100.100.200/",                    # Alibaba Cloud IMDS
            "http://[::ffff:127.0.0.1]/",                 # v4-mapped loopback
        ):
            with self.assertRaises(m._UnsafeUrl, msg=url):
                m._assert_base_url_safe(url)

    def test_blocked_hostnames_rejected(self):
        m = self._mod()
        for url in ("http://localhost/", "http://metadata.google.internal/", "http://metadata/"):
            with self.assertRaises(m._UnsafeUrl, msg=url):
                m._assert_scheme_and_static_host(url)

    def test_hostname_resolving_to_private_rejected(self):
        """DNS-rebind-to-private: a public-looking name whose A record is private."""
        m = self._mod()
        with patch.object(m.socket, "getaddrinfo", return_value=_addrinfo("192.168.5.5")):
            with self.assertRaises(m._UnsafeUrl):
                m._assert_base_url_safe("https://sneaky.example.com/")

    def test_hostname_resolving_to_nothing_rejected(self):
        """Empty resolution must fail closed, not pass."""
        m = self._mod()
        with patch.object(m.socket, "getaddrinfo", return_value=[]):
            with self.assertRaises(m._UnsafeUrl):
                m._assert_base_url_safe("https://void.example.com/")

    def test_public_hostname_accepted(self):
        m = self._mod()
        with patch.object(m.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            m._assert_base_url_safe("https://example.com/")  # must not raise

    def test_redirect_handler_refuses_all(self):
        m = self._mod()
        h = m._NoRedirectHandler()
        with self.assertRaises(m._UnsafeUrl):
            h.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/")

    # ── PENTEST-7: DNS-rebind TOCTOU is closed by IP-pinning ──────────────────

    def test_validated_pinned_ip_returns_first_validated_address(self):
        """The guard resolves ONCE and returns the address to pin the connect to."""
        m = self._mod()
        with patch.object(m.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            self.assertEqual(
                m._validated_pinned_ip("https://example.com/"), "93.184.216.34",
            )

    def test_pinned_connection_connects_to_pinned_ip_not_reresolved_host(self):
        """A pinned connection connects to the validated IP and keeps the
        original hostname (Host header / SNI) — so a ~0-TTL rebind at connect
        time can never redirect the fetch to a private/metadata address."""
        m = self._mod()
        conn = m._PinnedHTTPConnection("rebind.example.com", port=80)
        conn._pinned_ip = "93.184.216.34"
        captured: dict = {}

        def _fake_create_connection(addr, *a, **k):
            captured["addr"] = addr
            raise ConnectionError("stop-before-send")

        with patch.object(m.socket, "create_connection", _fake_create_connection):
            with self.assertRaises(ConnectionError):
                conn.connect()
        # Connected to the pinned IP, NOT a re-resolution of the hostname.
        self.assertEqual(captured["addr"], ("93.184.216.34", 80))
        # Host header / cert identity stays the original hostname.
        self.assertEqual(conn.host, "rebind.example.com")

    def test_pinned_opener_wires_pinned_handlers(self):
        m = self._mod()
        opener = m._build_no_redirect_opener("93.184.216.34")
        # A no-redirect handler plus at least the pinned HTTP handler are wired.
        self.assertTrue(
            any(isinstance(h, m._PinnedHTTPHandler) for h in opener.handlers)
        )
        self.assertTrue(
            any(isinstance(h, m._NoRedirectHandler) for h in opener.handlers)
        )


# ── Registration-time rejection (HTTP, via TestClient) ─────────────────────────

class TestRegistrationRejectsUnsafe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _put(self, client, adapter_id, body):
        return client.put(f"/v1/console/data-sources/adapters/http/{adapter_id}", json=body)

    def test_metadata_ip_rejected(self):
        body = {"display_name": "x", "base_url": "http://169.254.169.254/latest/meta-data/",
                "auth_type": "none", "locality": "us_cloud", "network_egress": "full"}
        with _sandbox(Path(self._tmp)) as (client, *_), _unlimited_license():
            resp = self._put(client, "meta", body)
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_localhost_rejected(self):
        body = {"display_name": "x", "base_url": "http://localhost:8000/",
                "auth_type": "none", "locality": "local", "network_egress": "full"}
        with _sandbox(Path(self._tmp)) as (client, *_), _unlimited_license():
            resp = self._put(client, "loc", body)
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_non_http_scheme_rejected(self):
        body = {"display_name": "x", "base_url": "file:///etc/passwd",
                "auth_type": "none", "locality": "local", "network_egress": "full"}
        with _sandbox(Path(self._tmp)) as (client, *_), _unlimited_license():
            resp = self._put(client, "sch", body)
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_arbitrary_auth_env_rejected(self):
        """Exfil vector: naming an arbitrary process env var as the credential."""
        body = {"display_name": "x", "base_url": "https://example.com/",
                "auth_type": "bearer", "auth_env": "ANTHROPIC_API_KEY",
                "locality": "us_cloud", "network_egress": "full"}
        with _sandbox(Path(self._tmp)) as (client, *_), _unlimited_license():
            resp = self._put(client, "exf", body)
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("CORVIN_DS_", resp.text)

    def test_allowlisted_auth_env_accepted(self):
        body = {"display_name": "x", "base_url": "https://example.com/",
                "auth_type": "bearer", "auth_env": "CORVIN_DS_MY_TOKEN",
                "locality": "us_cloud", "network_egress": "full"}
        with _sandbox(Path(self._tmp)) as (client, *_), _unlimited_license():
            resp = self._put(client, "ok", body)
        self.assertEqual(resp.status_code, 200, resp.text)


# ── Ping-time behaviour (the fetch site) ───────────────────────────────────────

class _FakeResp:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


class _FakeOpener:
    """Captures the Request and returns a canned /ping body."""
    def __init__(self, payload: dict):
        self.payload = payload
        self.last_request = None

    def open(self, req, timeout=None):
        self.last_request = req
        return _FakeResp(self.payload)


class TestPingBehaviour(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_manifest(self, home, tid, adapter_id, manifest):
        d = home / "tenants" / tid / "global" / "datasources" / "http"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{adapter_id}.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _ping(self, client, adapter_id):
        return client.post(f"/v1/console/data-sources/adapters/http/{adapter_id}/ping")

    def test_ping_no_egress_declared_refused(self):
        """network_egress='none' (the default) → outbound ping refused."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, _):
            self._write_manifest(home, tid, "noeg", {
                "adapter_id": "noeg", "_base_url": "https://example.com",
                "auth_type": "none", "network_egress": "none",
            })
            resp = self._ping(client, "noeg")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("error"), "egress_not_declared")

    def test_ping_dns_to_private_blocked(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, _):
            self._write_manifest(home, tid, "reb", {
                "adapter_id": "reb", "_base_url": "https://rebind.example.com",
                "auth_type": "none", "network_egress": "full",
            })
            import corvin_console.routes.datasources_http as m
            with patch.object(m.socket, "getaddrinfo", return_value=_addrinfo("10.1.2.3")):
                resp = self._ping(client, "reb")
        self.assertEqual(resp.status_code, 200, resp.text)
        j = resp.json()
        self.assertFalse(j.get("reachable"))
        self.assertEqual(j.get("error"), "egress_blocked")

    def test_ping_legit_public_https_with_cred_var_works(self):
        os.environ["CORVIN_DS_MY_TOKEN"] = "s3cr3t-adapter-token"
        try:
            with _sandbox(Path(self._tmp)) as (client, home, tid, _):
                self._write_manifest(home, tid, "good", {
                    "adapter_id": "good", "_base_url": "https://example.com",
                    "auth_type": "bearer", "auth_env": "CORVIN_DS_MY_TOKEN",
                    "network_egress": "full",
                })
                import corvin_console.routes.datasources_http as m
                fake = _FakeOpener({"name": "demo-adapter", "version": "1.2.3"})
                with patch.object(m.socket, "getaddrinfo",
                                  return_value=_addrinfo("93.184.216.34")), \
                     patch.object(m, "_build_no_redirect_opener", return_value=fake):
                    resp = self._ping(client, "good")
            self.assertEqual(resp.status_code, 200, resp.text)
            j = resp.json()
            self.assertTrue(j.get("ok"))
            self.assertTrue(j.get("reachable"))
            self.assertEqual(j.get("version"), "1.2.3")
            # The allowlisted credential WAS attached (legit auth still works).
            self.assertEqual(
                fake.last_request.get_header("Authorization"),
                "Bearer s3cr3t-adapter-token",
            )
        finally:
            os.environ.pop("CORVIN_DS_MY_TOKEN", None)

    def test_ping_legacy_arbitrary_auth_env_not_leaked(self):
        """A manifest written before the gate with an arbitrary auth_env must NOT
        have that secret attached at ping time (defense-in-depth)."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-must-not-leak"
        try:
            with _sandbox(Path(self._tmp)) as (client, home, tid, _):
                self._write_manifest(home, tid, "legacy", {
                    "adapter_id": "legacy", "_base_url": "https://example.com",
                    "auth_type": "bearer", "auth_env": "ANTHROPIC_API_KEY",
                    "network_egress": "full",
                })
                import corvin_console.routes.datasources_http as m
                fake = _FakeOpener({"name": "n", "version": "0"})
                with patch.object(m.socket, "getaddrinfo",
                                  return_value=_addrinfo("93.184.216.34")), \
                     patch.object(m, "_build_no_redirect_opener", return_value=fake):
                    resp = self._ping(client, "legacy")
            self.assertEqual(resp.status_code, 200, resp.text)
            # No Authorization header → the arbitrary env var was never read/sent.
            self.assertIsNone(fake.last_request.get_header("Authorization"))
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
