"""E2E: the Console marketplace install flow, through the REAL FastAPI route.

This exercises ``POST /v1/console/api/v1/marketplace/plugins/{index_id}/install``
(and its uninstall sibling) over a ``TestClient`` — the actual transport
boundary, not a direct function call — and proves the loop the operator sees:

    install a real builtin  →  registry.yaml gains it
                            →  GET /v1/console/plugins lists it (installed)
                            →  uninstall  →  it is gone again

Plus the boot path: a builtin LOADED in-process (``bootstrap_builtin``) that was
never explicitly installed must still surface under ``GET /v1/console/plugins``
via the ``_running_builtins`` merge.

Sandbox pattern (module purge + restore, CORVIN_HOME redirect) is lifted from
``test_plugins_route.py`` so the app re-reads this test's tenant home. The
marketplace SOURCE + index come from the sibling ``../Corvin-Marketplace``
checkout (operator rule); the whole module skips when that checkout is absent.

Target builtin: ``plugin:buildin-memory-semantic_context_retriever`` — the one
builtin that both resolves locally AND passes the ADR-0247 manifest gate with a
known ``plugin_type`` (``context_retriever``). Its registry key (manifest
``plugin_id``) is the hyphenated ``semantic-context-retriever``, which is exactly
the index/manifest id divergence ``marketplace_resolve`` bridges.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"
_PLUGINS = _REPO / "core" / "plugins"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED), str(_PLUGINS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MARKETPLACE = _REPO.parent / "Corvin-Marketplace"
_MKT_BUILDIN = _MARKETPLACE / "plugins" / "buildin"
_MKT_INDEX = _MARKETPLACE / "index" / "plugins.json"

# The one builtin that resolves locally AND passes the ADR-0247 gate today.
_INDEX_ID = "plugin:buildin-memory-semantic_context_retriever"
_REGISTRY_ID = "semantic-context-retriever"
_INSTALL_BASE = "/v1/console/api/v1/marketplace/plugins"

_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")


def _snapshot_modules() -> dict:
    return {k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)}


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED_PREFIXES):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _sandbox(tmp_path: Path):
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH",
            "CORVIN_MARKETPLACE_ROOT", "CORVIN_MARKETPLACE_INDEX")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    # SOURCE + index from the sibling marketplace checkout (operator rule).
    os.environ["CORVIN_MARKETPLACE_ROOT"] = str(_MKT_BUILDIN)
    os.environ["CORVIN_MARKETPLACE_INDEX"] = str(_MKT_INDEX)

    preloaded = _snapshot_modules()
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, _auth.derive_csrf_token(rec.csrf_secret, rec.sid), home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


@unittest.skipUnless(
    _MKT_BUILDIN.is_dir() and _MKT_INDEX.is_file(),
    "sibling Corvin-Marketplace checkout (source + index) not present",
)
class TestMarketplaceInstallE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hdr(self, csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    def _flag(self, client, csrf, flag_id: str, on: bool) -> None:
        resp = client.put(
            f"/v1/console/settings/features/{flag_id}",
            json={"enabled": on},
            headers=self._hdr(csrf),
        )
        assert resp.status_code == 200, resp.text

    @contextmanager
    def _live(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            yield client, csrf, home

    def _installed_ids(self, client) -> set[str]:
        resp = client.get("/v1/console/plugins")
        assert resp.status_code == 200, resp.text
        return {p["plugin_id"] for p in resp.json()["plugins"]}

    def test_install_lists_then_uninstall_removes(self):
        with self._live() as (client, csrf, home):
            registry_path = home / "tenants" / "_default" / "plugins" / "registry.yaml"

            # Precondition: not installed, no registry yet.
            self.assertNotIn(_REGISTRY_ID, self._installed_ids(client))

            # INSTALL through the real route.
            resp = client.post(
                f"{_INSTALL_BASE}/{_INDEX_ID}/install",
                json={"version": "1.0.0"},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["status"], "completed", body)
            self.assertEqual(body["registry_id"], _REGISTRY_ID, body)

            # registry.yaml was really written and contains the plugin.
            self.assertTrue(registry_path.exists(), "install must write registry.yaml")
            import yaml

            data = yaml.safe_load(registry_path.read_text()) or {}
            self.assertIn(_REGISTRY_ID, data.get("plugins", {}),
                          "registry.yaml must contain the installed builtin")

            # GET /plugins now lists it (installed, disabled by default).
            resp = client.get("/v1/console/plugins")
            self.assertEqual(resp.status_code, 200, resp.text)
            entry = {p["plugin_id"]: p for p in resp.json()["plugins"]}.get(_REGISTRY_ID)
            self.assertIsNotNone(entry, "installed builtin must appear in GET /plugins")
            self.assertFalse(entry["enabled"], "install must not enable")

            # UNINSTALL through the real route (index-id is bridged to registry key).
            resp = client.post(
                f"{_INSTALL_BASE}/{_INDEX_ID}/uninstall",
                json={},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["registry_id"], _REGISTRY_ID)

            # Gone from the registry-backed listing.
            self.assertNotIn(_REGISTRY_ID, self._installed_ids(client))

    def test_install_is_idempotent(self):
        with self._live() as (client, csrf, _home):
            first = client.post(
                f"{_INSTALL_BASE}/{_INDEX_ID}/install", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(first.status_code, 200, first.text)
            second = client.post(
                f"{_INSTALL_BASE}/{_INDEX_ID}/install", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(second.status_code, 200, second.text)
            self.assertTrue(second.json().get("already_installed"), second.text)

    def test_unknown_index_id_fails_cleanly(self):
        with self._live() as (client, csrf, _home):
            resp = client.post(
                f"{_INSTALL_BASE}/plugin:buildin-memory-does_not_exist/install",
                json={},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)  # job carries the failure
            body = resp.json()
            self.assertEqual(body["status"], "failed", body)
            self.assertIn("not in the marketplace index", body["error"])

    def test_running_builtin_surfaces_without_an_install_click(self):
        """A builtin loaded at boot (never installed) still shows under /plugins."""
        with self._live() as (client, _csrf, _home):
            from corvin_plugins.bootstrap import build_context
            from corvin_plugins.protocol import HealthStatus
            from corvin_plugins.registry import get_registry

            class _Live:
                plugin_id = _REGISTRY_ID
                plugin_type = "context_retriever"
                version = "0.1.0"
                display_name = "Semantic Context Retriever"

                def on_load(self, ctx):
                    pass

                def on_unload(self):
                    pass

                def health_check(self):
                    return HealthStatus(ok=True)

                def retrieve(self, *a, **k):
                    return []

            get_registry().register(
                _Live(),
                build_context(
                    plugin_id=_REGISTRY_ID, tenant_id="_default", corvin_home=Path("/tmp")
                ),
            )
            try:
                entry = {p["plugin_id"]: p for p in
                         client.get("/v1/console/plugins").json()["plugins"]}.get(_REGISTRY_ID)
                self.assertIsNotNone(
                    entry, "a running builtin must surface under GET /plugins"
                )
                self.assertEqual(entry["origin"], "builtin",
                                 "origin is location-derived for a running builtin")
            finally:
                try:
                    get_registry().unregister(_REGISTRY_ID)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
