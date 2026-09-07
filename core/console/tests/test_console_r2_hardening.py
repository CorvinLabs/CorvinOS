"""R2-C1 / R2-C2 / R2-C4 + round-2 follow-ups — console hardening regressions.

Each test drives the REAL boundary (an HTTP request through the mounted app,
or the shipped guard function the route calls) — never a re-implementation.

  * R2-C1  ``source_url`` comes from a REMOTE RAG provider and is rendered as
           an ``<a href>``. Both the query engine's mapper and the console API
           now allowlist absolute http(s) URLs, so a ``javascript:`` href can
           never reach the browser. (The matching frontend guard is
           ``src/lib/safe-url.ts`` + ``tests/unit/lib/safe-url.test.ts``.)
  * R2-C2  the SSRF guard missed 100.64.0.0/10 (RFC 6598 shared address space):
           ``is_private`` is False for it on py3.11. The guard is now
           ``not is_global`` plus the explicit flags, and the same shared
           predicate backs ``custom_provider``.
  * R2-C4  ``_SPAStaticFiles`` answered 200 text/html for a MISSING
           ``assets/*.js``, so a browser holding a stale shell executed HTML as
           a script. Missing assets are 404; client-side routes still fall back.
  * follow-up  ``rag_hub.import_provider`` writes a provider manifest whose
           ``endpoint`` the server later fetches — it now runs the same
           SSRF/egress guard ``custom_provider.create`` runs.
  * follow-up  ``custom_provider.test-api`` pins the validated IP for the
           actual fetch (DNS-rebinding TOCTOU).
"""
from __future__ import annotations

import ipaddress
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


# ── R2-C4: the SPA fallback must not answer for missing assets ────────────────

class SpaAssetFallbackTests(unittest.TestCase):
    @contextmanager
    def _client(self):
        tmp = Path(tempfile.mkdtemp())
        dist = tmp / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            "<html><body>SPA SHELL</body></html>", encoding="utf-8")
        (dist / "assets" / "index-abc123.js").write_text(
            "console.log('real bundle');", encoding="utf-8")
        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME",)}
        os.environ["CORVIN_HOME"] = str(tmp / "corvin_home")
        try:
            _reset_modules()
            from corvin_console.app import mount_static
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            app = FastAPI()
            with patch("corvin_console.app._NEXT_DIST_DIR", dist), \
                 patch("corvin_console.app.headless_enabled", return_value=False):
                mount_static(app, url_prefix="/console")
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _reset_modules()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_hashed_bundle_is_404_not_the_spa_shell(self):
        """The finding: a stale shell requesting a deleted bundle got 200
        text/html and tried to execute the SPA HTML as JavaScript."""
        with self._client() as client:
            resp = client.get("/console/assets/index-DELETED.js")
            self.assertEqual(resp.status_code, 404, resp.text[:200])
            self.assertNotIn("SPA SHELL", resp.text)

    def test_missing_asset_of_every_type_is_404(self):
        with self._client() as client:
            for path in ("assets/style-old.css", "assets/nested/chunk.js",
                         "assets/logo.svg", "assets/"):
                resp = client.get(f"/console/{path}")
                self.assertEqual(resp.status_code, 404, f"{path}: {resp.status_code}")

    def test_existing_asset_still_served_and_immutable(self):
        with self._client() as client:
            resp = client.get("/console/assets/index-abc123.js")
            self.assertEqual(resp.status_code, 200, resp.text[:200])
            self.assertIn("real bundle", resp.text)
            self.assertIn("immutable", resp.headers.get("cache-control", ""))

    def test_client_side_route_still_falls_back_to_the_shell(self):
        """The fallback must keep working for ROUTES — only assets/ is exempt."""
        with self._client() as client:
            for route in ("/console/settings", "/console/tasks/abc", "/console/"):
                resp = client.get(route)
                self.assertEqual(resp.status_code, 200, f"{route}: {resp.status_code}")
                self.assertIn("SPA SHELL", resp.text)
                self.assertEqual(resp.headers.get("cache-control"), "no-cache", route)


# ── R2-C2: the SSRF guard covers the shared address space ─────────────────────

class SsrfSharedAddressSpaceTests(unittest.TestCase):
    def _mods(self):
        _reset_modules()
        import importlib
        ds = importlib.import_module("corvin_console.routes.datasources_http")
        cp = importlib.import_module("corvin_console.routes.custom_provider")
        return ds, cp

    def test_cgnat_and_other_non_global_ranges_are_blocked(self):
        ds, _cp = self._mods()
        # 100.64.0.0/10 is the finding: is_private is False on py3.11.
        self.assertFalse(ipaddress.ip_address("100.64.0.1").is_private)
        for ip in ("100.64.0.1", "100.127.255.254", "100.100.100.200",
                   "192.0.0.192", "0.0.0.0", "240.0.0.1", "198.18.0.1",
                   "192.0.2.1", "224.0.0.1", "10.0.0.1", "169.254.169.254"):
            self.assertTrue(ds._ip_is_blocked(ipaddress.ip_address(ip)), ip)

    def test_public_addresses_still_pass(self):
        ds, _cp = self._mods()
        for ip in ("1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"):
            self.assertFalse(ds._ip_is_blocked(ipaddress.ip_address(ip)), ip)

    def test_custom_provider_shares_the_same_predicate(self):
        """The two routes must not drift — custom_provider imports the guard."""
        ds, cp = self._mods()
        self.assertIs(cp._ip_is_blocked, ds._ip_is_blocked)
        with self.assertRaises(ds._UnsafeUrl):
            cp._assert_provider_endpoint_allowed("http://100.64.0.1/")

    def test_static_url_guard_rejects_cgnat_literal(self):
        ds, _cp = self._mods()
        with self.assertRaises(ds._UnsafeUrl):
            ds._assert_scheme_and_static_host("http://100.64.0.1/x")


# ── follow-up: test-api pins the validated IP (DNS rebinding) ────────────────

class ProviderEndpointPinTests(unittest.TestCase):
    def _cp(self):
        _reset_modules()
        import importlib
        return importlib.import_module("corvin_console.routes.custom_provider")

    def _addrinfo(self, ip: str):
        fam = 10 if ":" in ip else 2
        return [(fam, 1, 6, "", (ip, 0))]

    def test_guard_returns_the_validated_ip_for_a_hostname(self):
        cp = self._cp()
        with patch("socket.getaddrinfo", return_value=self._addrinfo("93.184.216.34")):
            self.assertEqual(cp._validated_provider_pin("https://example.com/s"),
                             "93.184.216.34")

    def test_literal_and_loopback_need_no_pin(self):
        cp = self._cp()
        self.assertIsNone(cp._validated_provider_pin("http://127.0.0.1:11434/api"))
        self.assertIsNone(cp._validated_provider_pin("http://localhost:11434/api"))
        self.assertIsNone(cp._validated_provider_pin("https://93.184.216.34/s"))

    def test_rebinding_second_answer_cannot_be_reached(self):
        """A ~0-TTL record answers public to the guard and metadata to the
        connect. The pin makes the fetch go to the FIRST, validated address."""
        cp = self._cp()
        answers = [self._addrinfo("93.184.216.34"), self._addrinfo("169.254.169.254")]
        with patch("socket.getaddrinfo", side_effect=answers):
            pin = cp._validated_provider_pin("https://rebind.attacker.example/x")
        self.assertEqual(pin, "93.184.216.34")
        url = cp._pinned_request_url("https://rebind.attacker.example/x", pin)
        self.assertEqual(url, "https://93.184.216.34/x")
        self.assertNotIn("rebind.attacker.example", url)

    def test_pinned_url_preserves_port_path_and_brackets_ipv6(self):
        cp = self._cp()
        self.assertEqual(
            cp._pinned_request_url("http://host.example:8080/a/b?c=1", "203.0.113.7"),
            "http://203.0.113.7:8080/a/b?c=1")
        self.assertEqual(
            cp._pinned_request_url("https://host.example/x", "2606:4700::1111"),
            "https://[2606:4700::1111]/x")

    def test_blocked_resolution_still_raises_before_any_pin(self):
        cp = self._cp()
        with patch("socket.getaddrinfo", return_value=self._addrinfo("169.254.169.254")):
            with self.assertRaises(cp._UnsafeUrl):
                cp._validated_provider_pin("https://evil.example/x")


# ── follow-up: rag_hub /import runs the SSRF guard ───────────────────────────

class RagHubImportGuardTests(unittest.TestCase):
    def _hub(self):
        _reset_modules()
        import importlib
        return importlib.import_module("corvin_console.routes.rag_hub")

    def _manifest(self, endpoint: str) -> str:
        return (
            "apiVersion: rag.corvin.io/v1alpha1\n"
            "kind: RAGProvider\n"
            "metadata:\n  name: imported\n"
            "spec:\n  retrieval:\n"
            f"    endpoint: {endpoint}\n"
            "    method: POST\n"
            "    auth:\n      type: none\n"
        )

    def test_metadata_and_private_endpoints_are_refused(self):
        hub = self._hub()
        for endpoint in ("http://169.254.169.254/latest/meta-data/",
                         "http://10.0.0.5:9200/search",
                         "http://100.64.0.1/search",
                         "file:///etc/passwd",
                         "http://192.0.0.192/"):
            with self.assertRaises(Exception, msg=endpoint) as ctx:
                hub._assert_manifest_endpoints_allowed(self._manifest(endpoint))
            self.assertEqual(type(ctx.exception).__name__, "_UnsafeUrl", endpoint)

    def test_public_endpoint_passes(self):
        hub = self._hub()
        with patch("socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            hub._assert_manifest_endpoints_allowed(
                self._manifest("https://api.example.com/search"))

    def test_endpoints_are_found_at_any_depth_and_under_any_url_key(self):
        hub = self._hub()
        deep = (
            "apiVersion: rag.corvin.io/v1alpha1\n"
            "kind: RAGProvider\n"
            "metadata:\n  name: deep\n"
            "spec:\n"
            "  retrieval:\n    endpoint: https://ok.example/s\n"
            "  extras:\n"
            "    - name: webhook\n      url: http://169.254.169.254/\n"
        )
        urls = hub._iter_manifest_endpoints(deep)
        self.assertIn("http://169.254.169.254/", urls)
        with self.assertRaises(Exception):
            hub._assert_manifest_endpoints_allowed(deep)

    def test_unparsable_manifest_yields_no_endpoints(self):
        """An invalid manifest is rejected downstream by validate_manifest —
        the guard must not crash on it."""
        hub = self._hub()
        self.assertEqual(hub._iter_manifest_endpoints("::: not yaml :::"), [])
        hub._assert_manifest_endpoints_allowed("")


# ── R2-C1: source_url allowlist on the backend ───────────────────────────────

class SourceUrlAllowlistTests(unittest.TestCase):
    HOSTILE = [
        "javascript:alert(document.domain)",
        "JavaScript:alert(1)",
        "  javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "//evil.example/path",
        "/relative/path",
        "not a url",
        "",
        None,
        123,
    ]
    OK = ["http://example.com/a", "https://example.com/a?b=1#c",
          "https://sub.example.com:8443/x"]

    def test_query_engine_mapper_drops_hostile_hrefs(self):
        from shared.rag_query_engine import safe_http_url

        for value in self.HOSTILE:
            self.assertIsNone(safe_http_url(value), repr(value))
        for value in self.OK:
            self.assertEqual(safe_http_url(value), value)

    def test_console_api_helper_drops_hostile_hrefs(self):
        _reset_modules()
        import importlib
        rag = importlib.import_module("corvin_console.routes.rag")
        for value in self.HOSTILE:
            self.assertIsNone(rag._safe_source_url(value), repr(value))
        for value in self.OK:
            self.assertEqual(rag._safe_source_url(value), value)

    def test_newline_smuggled_scheme_is_rejected(self):
        """`java\\nscript:` survives a naive prefix check because browsers strip
        control characters before parsing the scheme."""
        from shared.rag_query_engine import safe_http_url

        self.assertIsNone(safe_http_url("java\nscript:alert(1)"))
        self.assertIsNone(safe_http_url("http://exa\rmple.com/"))

    def test_engine_transform_response_sanitises_provider_output(self):
        """Through the real mapper: a hostile provider payload yields an item
        with source_url=None, not a javascript: href."""
        import importlib

        eng = importlib.import_module("shared.rag_query_engine")
        cls = eng.RAGQueryEngine
        obj = cls.__new__(cls)
        items = cls._transform_response(obj, {"results": [
            {"content": "x", "score": 0.9, "source_url": "javascript:alert(1)"},
            {"content": "y", "score": 0.5, "source_url": "https://ok.example/doc"},
        ]})
        self.assertIsNone(items[0].source_url)
        self.assertEqual(items[1].source_url, "https://ok.example/doc")


if __name__ == "__main__":
    unittest.main()
