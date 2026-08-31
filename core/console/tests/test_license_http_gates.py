"""HTTP route-level proof tests for license gate enforcement — ADR-0092/ADR-0094.

These tests go one layer deeper than test_license_proof.py (which tests the
validator.py module in isolation) and test_license_limits_e2e.py (which tests
the module-level gate functions): they fire actual HTTP requests through a real
FastAPI TestClient and verify that the routes return the correct HTTP status
codes when license limits are exceeded.

Proven gates:
  A. POST /custom-provider/create → 403 without CSRF token
  B. POST /custom-provider/create → 402 when rag_providers_max exceeded
  C. POST /data-sources → 402 when datasource_adapters_allowed blocks adapter
  D. POST /remote-trigger/pair/redeem → 402 when a2a_peers_max exceeded
  E. Source-level check: a2a_peers_max gate is wired in pair_redeem
  F. Source-level check: custom-provider create uses require_csrf (not require_session)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_ROUTES = _CONSOLE / "corvin_console" / "routes"

for _p in [
    str(_OPERATOR),
    str(_OPERATOR / "license"),
    str(_OPERATOR / "forge"),
    str(_CONSOLE),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path, *, set_csrf: bool = True):
    """Spin up a sandboxed console app with a live session."""
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
        if set_csrf:
            client.headers.update({"X-CSRF-Token": csrf})

        yield client, home, tenant_id, csrf
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


# ── A: CSRF enforcement on /custom-provider/create ────────────────────────────

class TestCustomProviderCsrfGate(unittest.TestCase):
    """POST /custom-provider/create requires X-CSRF-Token."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_without_csrf_returns_403(self):
        """Missing CSRF token → 403 Forbidden (not 401, not 200)."""
        with _sandbox(Path(self._tmp), set_csrf=False) as (client, home, tid, csrf):
            resp = client.post(
                "/v1/console/custom-provider/create",
                json={"provider_id": "test", "name": "Test Provider"},
                # No X-CSRF-Token header
            )
            self.assertEqual(resp.status_code, 403,
                f"Expected 403 without CSRF, got {resp.status_code}: {resp.text}")

    def test_create_with_wrong_csrf_returns_403(self):
        """Wrong CSRF token → 403 Forbidden."""
        with _sandbox(Path(self._tmp), set_csrf=False) as (client, home, tid, csrf):
            resp = client.post(
                "/v1/console/custom-provider/create",
                json={"provider_id": "test", "name": "Test Provider"},
                headers={"X-CSRF-Token": "definitely-wrong-token"},
            )
            self.assertEqual(resp.status_code, 403,
                f"Expected 403 with wrong CSRF, got {resp.status_code}: {resp.text}")


# ── B: rag_providers_max via HTTP ─────────────────────────────────────────────

class TestCustomProviderRagLimit(unittest.TestCase):
    """POST /custom-provider/create → 402 when rag_providers_max exceeded."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_rag_limit_returns_402(self):
        """With free tier (max=1) and 1 existing provider, create → 402."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Create the RAG registry dir and plant 1 existing provider YAML.
            rag_dir = home / "tenants" / tid / "global" / "rag"
            rag_dir.mkdir(parents=True, exist_ok=True)
            (rag_dir / "existing-provider.yaml").write_text(
                "provider_id: existing-provider\nname: Existing\n", encoding="utf-8"
            )

            # Patch get_limit to return free-tier limit of 1.
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                side_effect=lambda f: 1 if f == "rag_providers_max" else None,
            ):
                resp = client.post(
                    "/v1/console/custom-provider/create",
                    json={"provider_id": "new-provider", "name": "New Provider"},
                )

            self.assertEqual(resp.status_code, 402,
                f"Expected 402 for exceeded rag_providers_max, got {resp.status_code}: {resp.text}")
            body = resp.json()
            detail = body.get("detail", {})
            self.assertEqual(detail.get("error"), "license_limit")
            self.assertEqual(detail.get("feature"), "rag_providers_max")

    def test_rag_limit_not_triggered_when_under_limit(self):
        """With max=2 and 1 existing provider, gate does NOT block (proceeds to create logic)."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            rag_dir = home / "tenants" / tid / "global" / "rag"
            rag_dir.mkdir(parents=True, exist_ok=True)
            (rag_dir / "existing-provider.yaml").write_text(
                "provider_id: existing-provider\nname: Existing\n", encoding="utf-8"
            )

            # Patch to return limit=2 (free tier has 1, this simulates member tier).
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                side_effect=lambda f: 2 if f == "rag_providers_max" else None,
            ):
                resp = client.post(
                    "/v1/console/custom-provider/create",
                    json={"provider_id": "new-provider", "name": "New Provider"},
                )

            # Gate passed — response is NOT 402 (may be 500 from missing manifest deps, that's fine).
            self.assertNotEqual(resp.status_code, 402,
                f"Got 402 even though limit=2 > existing=1: {resp.text}")

    def test_no_limit_when_lic_returns_none(self):
        """When _lic_get_limit returns None (unlimited), gate is skipped entirely."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Plant 100 providers to make the count wildly exceed any numeric limit.
            rag_dir = home / "tenants" / tid / "global" / "rag"
            rag_dir.mkdir(parents=True, exist_ok=True)
            for i in range(5):
                (rag_dir / f"provider-{i}.yaml").write_text(
                    f"provider_id: provider-{i}\nname: Provider {i}\n", encoding="utf-8"
                )

            # Patch to return None (enterprise/unlimited).
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                return_value=None,
            ):
                resp = client.post(
                    "/v1/console/custom-provider/create",
                    json={"provider_id": "new-provider", "name": "New Provider"},
                )

            self.assertNotEqual(resp.status_code, 402,
                f"Got 402 with unlimited license (None): {resp.text}")


# ── B2: rag_providers_max via the /hub/import write path (ADR-0144 CON-01) ────

class TestRagHubImportGate(unittest.TestCase):
    """POST /hub/import must enforce rag_providers_max — it writes into the SAME
    rag/ registry dir as custom_provider.create and was previously ungated."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    _MANIFEST = (
        "apiVersion: rag.corvin.io/v1alpha1\n"
        "kind: RAGProvider\n"
        "metadata:\n  name: imported-provider\n"
        "spec:\n  retrieval:\n    endpoint: https://example.com/q\n"
    )

    def test_import_blocked_when_at_limit(self):
        """Free tier (max=1) with 1 existing provider → /hub/import returns 402."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            rag_dir = home / "tenants" / tid / "global" / "rag"
            rag_dir.mkdir(parents=True, exist_ok=True)
            (rag_dir / "existing-provider.yaml").write_text(
                "provider_id: existing-provider\nname: Existing\n", encoding="utf-8"
            )
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                side_effect=lambda f: 1 if f == "rag_providers_max" else None,
            ):
                resp = client.post(
                    "/v1/console/hub/import",
                    json={"manifest_yaml": self._MANIFEST, "provider_id": "new-one"},
                )
            self.assertEqual(resp.status_code, 402,
                f"Expected 402 on import past rag_providers_max, got {resp.status_code}: {resp.text}")
            self.assertEqual(resp.json().get("detail", {}).get("feature"), "rag_providers_max")

    def test_import_path_traversal_rejected(self):
        """A provider_id with path separators / '..' → 400 (cannot escape rag/ dir)."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                return_value=None,  # unlimited — prove sanitize fires independently
            ):
                resp = client.post(
                    "/v1/console/hub/import",
                    json={"manifest_yaml": self._MANIFEST, "provider_id": "../../etc/evil"},
                )
            self.assertEqual(resp.status_code, 400,
                f"Expected 400 for traversal provider_id, got {resp.status_code}: {resp.text}")
            self.assertEqual(resp.json().get("detail", {}).get("error"), "invalid_provider_id")

    def test_import_allowed_under_limit(self):
        """Unlimited license (None) → import gate does not block with 402."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            rag_dir = home / "tenants" / tid / "global" / "rag"
            rag_dir.mkdir(parents=True, exist_ok=True)
            for i in range(5):
                (rag_dir / f"p-{i}.yaml").write_text(f"provider_id: p-{i}\n", encoding="utf-8")
            with patch(
                "corvin_console.routes._rag_license_gate._lic_get_limit",
                return_value=None,
            ):
                resp = client.post(
                    "/v1/console/hub/import",
                    json={"manifest_yaml": self._MANIFEST, "provider_id": "another"},
                )
            self.assertNotEqual(resp.status_code, 402,
                f"Got 402 with unlimited license: {resp.text}")


# ── C: datasource_adapters_allowed via HTTP ───────────────────────────────────

class TestDataSourceAdapterGate(unittest.TestCase):
    """POST /data-sources → 402 when adapter not in allowed list."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_non_local_file_adapter_blocked_on_free_tier(self):
        """Free tier only allows local_file — postgresql adapter → 402."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Patch the license gate so datasource_adapters_allowed = ["local_file"].
            with patch(
                "corvin_console.routes.data_sources._lic_get_limit",
                side_effect=lambda f: ["local_file"] if f == "datasource_adapters_allowed" else None,
            ):
                resp = client.post(
                    "/v1/console/data-sources",
                    json={"manifest": {"adapter": "postgresql", "name": "my-pg"}},
                )

            self.assertEqual(resp.status_code, 402,
                f"Expected 402 for blocked adapter, got {resp.status_code}: {resp.text}")
            body = resp.json()
            detail = body.get("detail", {})
            self.assertEqual(detail.get("error"), "license_limit")
            self.assertEqual(detail.get("feature"), "datasource_adapters_allowed")
            self.assertIn("postgresql", detail.get("adapter", ""))

    def test_local_file_adapter_allowed_on_free_tier(self):
        """local_file adapter is in the free-tier allowlist — gate does NOT block."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes.data_sources._lic_get_limit",
                side_effect=lambda f: ["local_file"] if f == "datasource_adapters_allowed" else None,
            ):
                resp = client.post(
                    "/v1/console/data-sources",
                    json={"manifest": {"adapter": "local_file", "name": "my-files"}},
                )

            # Gate passed — NOT 402.
            self.assertNotEqual(resp.status_code, 402,
                f"local_file should NOT be blocked by adapter gate: {resp.text}")

    def test_unlimited_none_allows_any_adapter(self):
        """When _lic_get_limit returns None, ALL adapters are allowed."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes.data_sources._lic_get_limit",
                return_value=None,
            ):
                resp = client.post(
                    "/v1/console/data-sources",
                    json={"manifest": {"adapter": "snowflake", "name": "my-dw"}},
                )

            self.assertNotEqual(resp.status_code, 402,
                f"snowflake should be allowed with unlimited license: {resp.text}")


# ── C2: datasource_adapters_allowed via the DSI-v2 HTTP path (ADR-0147 CON-DS-V2-01)

class TestDataSourceHttpV2Gate(unittest.TestCase):
    """PUT /data-sources/adapters/http/{id} must enforce datasource_adapters_allowed
    — the DSI-v2 registration path was ungated while the DSI-v1 path was gated."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    _BODY = {
        "display_name": "My Remote", "base_url": "https://example.com",
        "auth_type": "none", "auth_env": "", "auth_header": "",
        "locality": "us_cloud", "network_egress": "full", "description": "",
    }

    def test_http_adapter_blocked_on_free_tier(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes.datasources_http._lic_get_limit",
                side_effect=lambda f: ["local_file"] if f == "datasource_adapters_allowed" else None,
            ):
                resp = client.put("/v1/console/data-sources/adapters/http/my-remote", json=self._BODY)
            self.assertEqual(resp.status_code, 402,
                f"DSI-v2 HTTP adapter must be blocked on free tier, got {resp.status_code}: {resp.text}")
            self.assertEqual(resp.json().get("detail", {}).get("feature"), "datasource_adapters_allowed")

    def test_http_adapter_allowed_when_unlimited(self):
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes.datasources_http._lic_get_limit",
                return_value=None,
            ):
                resp = client.put("/v1/console/data-sources/adapters/http/my-remote2", json=self._BODY)
            self.assertNotEqual(resp.status_code, 402,
                f"unlimited license must not block DSI-v2 HTTP adapter: {resp.text}")


# ── D: a2a_peers_max via HTTP ─────────────────────────────────────────────────

class TestA2APeerLimitHttp(unittest.TestCase):
    """POST /remote-trigger/pair/redeem → 402 when a2a_peers_max exceeded."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._origins_dir = Path(self._tmp) / "remote_origins"
        self._origins_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_invite_code(self) -> str:
        """Generate a minimal (but valid-shaped) A2A invite code for testing."""
        invite = {
            "v": 1,
            "accept_id": "test-accept-id",
            "accept_url": "http://localhost:99999/remote-trigger/pair/accept",
            "accept_key": "a" * 64,
            "issuer_url": "http://issuer.example.com/a2a",
            "issuer_instance_id": "issuer-instance-id",
            "issuer_label": "Test Issuer",
            "origin_id": "peer-origin-id",
            "r2i_hmac_key": "b" * 64,
            "r2i_recv_key": "c" * 64,
            "i2r_hmac_key": "d" * 64,
            "i2r_recv_key": "e" * 64,
            "max_ttl_s": 300,
            "expires_at": time.time() + 3600,
        }
        return base64.urlsafe_b64encode(json.dumps(invite).encode()).decode()

    def test_redeem_blocked_at_free_tier_limit(self):
        """With 1 existing peer (free tier max=1), redeem → 402."""
        # Plant one existing origin file.
        (self._origins_dir / "existing-peer.json").write_text(
            '{"origin_id": "existing-peer", "enabled": true}', encoding="utf-8"
        )

        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with (
                patch("corvin_console.routes.a2a_pair._lic_get_limit",
                      side_effect=lambda f: 1 if f == "a2a_peers_max" else None),
                patch("corvin_console.routes.a2a_pair._origins_dir",
                      return_value=self._origins_dir),
            ):
                resp = client.post(
                    "/v1/console/remote-trigger/pair/redeem",
                    json={
                        "invite_code": self._make_invite_code(),
                        "our_url": "http://me.example.com/a2a",
                        "our_console_url": "http://me.example.com",
                        "our_label": "Me",
                        "our_origin_id": "my-origin-id",
                        "spawn_worker": False,
                    },
                )

        self.assertEqual(resp.status_code, 402,
            f"Expected 402 for exceeded a2a_peers_max, got {resp.status_code}: {resp.text}")
        body = resp.json()
        detail = body.get("detail", {})
        self.assertEqual(detail.get("error"), "license_limit")
        self.assertEqual(detail.get("feature"), "a2a_peers_max")

    def test_redeem_allowed_when_under_limit(self):
        """With 0 existing peers (free tier max=1), gate does NOT block."""
        # No existing origin files — count is 0.
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with (
                patch("corvin_console.routes.a2a_pair._lic_get_limit",
                      side_effect=lambda f: 1 if f == "a2a_peers_max" else None),
                patch("corvin_console.routes.a2a_pair._origins_dir",
                      return_value=self._origins_dir),
            ):
                resp = client.post(
                    "/v1/console/remote-trigger/pair/redeem",
                    json={
                        "invite_code": self._make_invite_code(),
                        "our_url": "http://me.example.com/a2a",
                        "our_console_url": "http://me.example.com",
                        "our_label": "Me",
                        "our_origin_id": "my-origin-id",
                        "spawn_worker": False,
                    },
                )

        # Gate passed (0 < 1) — response is NOT 402 (may fail at server-to-server call, that's OK).
        self.assertNotEqual(resp.status_code, 402,
            f"Got 402 even though 0 < 1 (under limit): {resp.text}")

    def test_redeem_unlimited_never_blocked(self):
        """With unlimited license (None), even 10 existing peers don't block."""
        for i in range(10):
            (self._origins_dir / f"peer-{i}.json").write_text(
                f'{{"origin_id": "peer-{i}", "enabled": true}}', encoding="utf-8"
            )

        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with (
                patch("corvin_console.routes.a2a_pair._lic_get_limit", return_value=None),
                patch("corvin_console.routes.a2a_pair._origins_dir",
                      return_value=self._origins_dir),
            ):
                resp = client.post(
                    "/v1/console/remote-trigger/pair/redeem",
                    json={
                        "invite_code": self._make_invite_code(),
                        "our_url": "http://me.example.com/a2a",
                        "our_console_url": "http://me.example.com",
                        "our_label": "Me",
                        "our_origin_id": "my-origin-id",
                        "spawn_worker": False,
                    },
                )

        self.assertNotEqual(resp.status_code, 402,
            f"Got 402 with unlimited license (None): {resp.text}")


# ── E: source-level wiring checks ────────────────────────────────────────────

class TestLicenseGateWiring(unittest.TestCase):
    """Verify that gate code is structurally present in the route files."""

    def _read(self, name: str) -> str:
        return (_ROUTES / name).read_text(encoding="utf-8")

    def test_a2a_pair_imports_lic_get_limit(self):
        """a2a_pair.py must import _lic_get_limit for the peer gate."""
        src = self._read("a2a_pair.py")
        self.assertIn("_lic_get_limit", src,
            "a2a_pair.py must define/import _lic_get_limit for the a2a_peers_max gate")

    def test_a2a_pair_checks_a2a_peers_max(self):
        """pair_redeem must check 'a2a_peers_max' before installing pair files."""
        src = self._read("a2a_pair.py")
        self.assertIn('"a2a_peers_max"', src,
            "a2a_pair.py must reference 'a2a_peers_max' for the license gate")

    def test_a2a_gate_precedes_file_install(self):
        """Gate check must appear BEFORE '1. Install endpoint file' comment."""
        src = self._read("a2a_pair.py")
        gate_pos = src.find('"a2a_peers_max"')
        install_pos = src.find("# 1. Install endpoint file")
        self.assertGreater(gate_pos, 0, "a2a_peers_max not found")
        self.assertGreater(install_pos, 0, "'# 1. Install endpoint file' not found")
        self.assertLess(gate_pos, install_pos,
            "License gate must appear BEFORE endpoint file install in pair_redeem")

    def test_custom_provider_create_uses_require_csrf(self):
        """POST /custom-provider/create must use require_csrf (not require_session)."""
        src = self._read("custom_provider.py")
        # Check that create_custom_provider uses require_csrf
        self.assertIn("require_csrf", src,
            "custom_provider.py must import require_csrf")
        # Find the create endpoint and verify it uses require_csrf
        create_idx = src.find("async def create_custom_provider")
        self.assertGreater(create_idx, 0, "create_custom_provider not found")
        # The function signature should use require_csrf, not require_session
        func_sig_end = src.find("-> dict[str, Any]:", create_idx)
        func_sig = src[create_idx:func_sig_end]
        self.assertIn("require_csrf", func_sig,
            f"create_custom_provider must use require_csrf in signature, found: {func_sig!r}")
        self.assertNotIn("require_session", func_sig,
            f"create_custom_provider must NOT use require_session (use require_csrf), found: {func_sig!r}")

    def test_data_sources_register_checks_adapter_allowlist(self):
        """POST /data-sources must check datasource_adapters_allowed."""
        src = self._read("data_sources.py")
        self.assertIn('"datasource_adapters_allowed"', src,
            "data_sources.py must reference 'datasource_adapters_allowed' for license gate")

    def test_data_sources_register_uses_require_csrf(self):
        """POST /data-sources must be CSRF-gated (state mutation)."""
        src = self._read("data_sources.py")
        self.assertIn("require_csrf", src,
            "data_sources.py must import and use require_csrf for mutations")

    def test_custom_provider_rag_providers_max_gate_exists(self):
        """Both RAG write paths must enforce rag_providers_max via the shared gate.

        ADR-0144 CON-01: the limit literal now lives in the single-source helper
        _rag_license_gate.py; both custom_provider.create AND rag_hub.import_provider
        must call enforce_rag_providers_max so the two write paths into the same
        rag/ registry dir can never drift (the import path was previously ungated).
        """
        gate_src = self._read("_rag_license_gate.py")
        self.assertIn('"rag_providers_max"', gate_src,
            "_rag_license_gate.py must reference 'rag_providers_max'")
        cp_src = self._read("custom_provider.py")
        self.assertIn("enforce_rag_providers_max", cp_src,
            "custom_provider.py must call the shared enforce_rag_providers_max gate")
        hub_src = self._read("rag_hub.py")
        self.assertIn("enforce_rag_providers_max", hub_src,
            "rag_hub.import_provider must call the shared rag_providers_max gate (CON-01)")

    def test_all_license_gates_return_402(self):
        """Every license limit gate must raise HTTP 402 (not 403 or 429)."""
        # 402 is the correct Payment Required status for license limits
        for route_file in ["custom_provider.py", "data_sources.py", "space.py"]:
            src = self._read(route_file)
            if "license_limit" in src:
                self.assertIn("402", src,
                    f"{route_file}: license gates must return HTTP 402")


# ── G: workflows_max via HTTP ─────────────────────────────────────────────────

class TestWorkflowsMaxHTTP(unittest.TestCase):
    """HTTP integration tests: workflows_max enforced on POST /workflows and
    POST /workflows/import via a real FastAPI TestClient.

    Each test exercises the full request path:
        CSRF check → per-tenant fcntl lock → count-existing → license gate → file write.

    Proved gates (ADR-0094):
        G1. POST /workflows → 200 for the first workflow on free tier
        G2. POST /workflows → 402 for the second workflow on free tier
        G3. DELETE /workflows/{wid} frees the slot; subsequent POST → 200
        G4. GET /workflows count reflects only successfully created workflows
        G5. POST /workflows/import → 402 when free-tier slot is taken
        G6. Unlimited tier (workflows_max=None): multiple creates all return 200
        G7. Duplicate-ID check (409) fires before the license gate (402)
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # Reset active license to free tier so workflows_max=1 is enforced.
        try:
            import license.validator as _v
            _v._ACTIVE_LICENSE = None
            _v._LICENSE_LOADED_AT = 0.0
        except ImportError:
            pass

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        try:
            import license.validator as _v
            _v._ACTIVE_LICENSE = None
        except ImportError:
            pass

    # ── G1 ───────────────────────────────────────────────────────────────────

    def test_first_create_succeeds_on_free_tier(self):
        """Free tier: the first POST /workflows returns 200 with ok=True."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            resp = client.post(
                "/v1/console/workflows",
                json={"id": "wf_alpha", "title": "Alpha"},
            )
            self.assertEqual(resp.status_code, 200,
                f"Expected 200 for first create on free tier, got {resp.status_code}: {resp.text}")
            body = resp.json()
            self.assertTrue(body.get("ok"), f"Expected ok=True: {body}")
            self.assertEqual(body["workflow"]["id"], "wf_alpha")

    # ── G2 ───────────────────────────────────────────────────────────────────

    def test_second_create_returns_402_with_license_detail(self):
        """Free tier: the second POST /workflows is blocked with 402 + license_limit detail."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            r1 = client.post("/v1/console/workflows", json={"id": "wf_one"})
            self.assertEqual(r1.status_code, 200,
                f"First create must succeed: {r1.text}")

            r2 = client.post("/v1/console/workflows", json={"id": "wf_two"})
            self.assertEqual(r2.status_code, 402,
                f"Expected 402 for second workflow on free tier, got {r2.status_code}: {r2.text}")

            detail = r2.json().get("detail", {})
            self.assertEqual(detail.get("error"), "license_limit",
                f"Wrong error type in 402 detail: {detail}")
            self.assertEqual(detail.get("feature"), "workflows_max",
                f"Wrong feature in 402 detail: {detail}")
            self.assertEqual(detail.get("limit"), 1,
                f"Wrong limit value in 402 detail: {detail}")
            self.assertIn("upgrade_url", detail,
                "402 detail must include upgrade_url for the upsell CTA")
            self.assertIn("msg", detail,
                "402 detail must include human-readable msg for the UI toast")

    # ── G3 ───────────────────────────────────────────────────────────────────

    def test_delete_frees_slot_for_next_create(self):
        """DELETE /workflows/{wid} frees the free-tier slot; subsequent POST → 200."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Fill the slot.
            r1 = client.post("/v1/console/workflows", json={"id": "wf_temp"})
            self.assertEqual(r1.status_code, 200)

            # Confirm slot is filled.
            r_blocked = client.post("/v1/console/workflows", json={"id": "wf_blocked"})
            self.assertEqual(r_blocked.status_code, 402,
                "Gate must block while slot is filled")

            # Delete the workflow.
            rd = client.delete("/v1/console/workflows/wf_temp")
            self.assertEqual(rd.status_code, 200,
                f"Delete must succeed: {rd.text}")

            # Slot is free — create another.
            r2 = client.post("/v1/console/workflows", json={"id": "wf_after_delete"})
            self.assertEqual(r2.status_code, 200,
                f"Expected 200 after slot freed by delete, got {r2.status_code}: {r2.text}")

    # ── G4 ───────────────────────────────────────────────────────────────────

    def test_list_workflows_count_matches_created(self):
        """GET /workflows count == number of successfully created workflows."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Empty initially.
            r0 = client.get("/v1/console/workflows")
            self.assertEqual(r0.status_code, 200)
            self.assertEqual(r0.json()["count"], 0)

            # Create one.
            client.post("/v1/console/workflows", json={"id": "wf_listed"})

            r1 = client.get("/v1/console/workflows")
            self.assertEqual(r1.status_code, 200)
            body = r1.json()
            self.assertEqual(body["count"], 1,
                f"Expected count=1 after one create: {body}")
            self.assertEqual(body["workflows"][0]["id"], "wf_listed")

            # Blocked second create must NOT appear in list.
            client.post("/v1/console/workflows", json={"id": "wf_ghost"})  # → 402
            r2 = client.get("/v1/console/workflows")
            self.assertEqual(r2.json()["count"], 1,
                "Blocked create must not increment the list count")

    # ── G5 ───────────────────────────────────────────────────────────────────

    def test_import_yaml_returns_402_when_at_limit(self):
        """POST /workflows/import → 402 when free-tier slot is already taken."""
        import io
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            # Fill the free-tier slot via create.
            r1 = client.post("/v1/console/workflows", json={"id": "wf_existing"})
            self.assertEqual(r1.status_code, 200,
                f"First create must succeed: {r1.text}")

            # Import must be blocked by the same _enforce_workflows_max gate.
            yaml_bytes = (
                b'awp: "1.0.0"\n'
                b'workflow:\n'
                b'  name: imported\n'
                b'orchestration:\n'
                b'  engine: dag\n'
                b'  graph: []\n'
            )
            resp = client.post(
                "/v1/console/workflows/import",
                files={"file": ("test.awp.yaml", io.BytesIO(yaml_bytes), "application/x-yaml")},
            )
            self.assertEqual(resp.status_code, 402,
                f"Expected 402 for import when at limit, got {resp.status_code}: {resp.text}")
            detail = resp.json().get("detail", {})
            self.assertEqual(detail.get("feature"), "workflows_max",
                f"Wrong feature in 402 detail: {detail}")

    # ── G6 ───────────────────────────────────────────────────────────────────

    def test_unlimited_tier_allows_multiple_creates(self):
        """Enterprise (workflows_max=None): all creates succeed, never 402."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            with patch(
                "corvin_console.routes.workflows._lic_get_limit",
                return_value=None,  # None = unlimited tier
            ):
                for i in range(3):
                    resp = client.post(
                        "/v1/console/workflows",
                        json={"id": f"wf_unlimited_{i}"},
                    )
                    self.assertEqual(resp.status_code, 200,
                        f"Unlimited tier: create {i + 1} must return 200, "
                        f"got {resp.status_code}: {resp.text}")

    # ── G7 ───────────────────────────────────────────────────────────────────

    def test_duplicate_id_returns_409_not_402(self):
        """409 CONFLICT fires before the license gate — duplicate-ID check has priority."""
        with _sandbox(Path(self._tmp)) as (client, home, tid, csrf):
            r1 = client.post("/v1/console/workflows", json={"id": "wf_dup"})
            self.assertEqual(r1.status_code, 200)

            # Same ID again: 409 fires first (meta file exists), not 402.
            r2 = client.post("/v1/console/workflows", json={"id": "wf_dup"})
            self.assertEqual(r2.status_code, 409,
                f"Duplicate ID must return 409, not 402, got {r2.status_code}: {r2.text}")


if __name__ == "__main__":
    unittest.main()
