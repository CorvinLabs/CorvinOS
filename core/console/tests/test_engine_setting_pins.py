"""ADR-0885 step 2b — pin parity, pin validation, strict registry status.

* ``GET /v1/console/settings/engine`` resolves the pins through the SAME
  function the cost optimizer and the worker spawn path use
  (``engine_models.get_tenant_engine_model``), so a whitespace-only pin
  cannot read "pinned" in the header and "adaptive" on the cost tab.
* ``PUT`` validates ``os_model``/``worker_model`` for the Claude-native path
  against the offline catalogue (422 on an unknown id) and REPLACES the
  whole ``engine_models`` map (documented, exercised).
* ``engine_models.registry_load_status()`` distinguishes "failed to load and
  nothing ever loaded" (→ 503 from ``claude_catalog_or_503``) from
  "loaded but empty" (accept) and from "stale-good cache after a later
  failure" (accept).
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

import engine_models  # type: ignore[import-not-found]  # noqa: E402
from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine as ENGINE  # noqa: E402
from corvin_console.routes import engine_api as EA  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MID = "claude-sonnet-5"


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class EngineSettingPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENGINE.router, prefix="/v1/console")
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    def _write_yaml(self, engine_models_block: str) -> None:
        p = Path(self._tmp.name) / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "apiVersion: corvin/v1\nkind: Tenant\nspec:\n  default_engine: claude_code\n"
            "  engine_models:\n" + engine_models_block
        )

    def test_get_resolves_pins_like_the_runtime(self) -> None:
        # whitespace-only os_model, real worker_model
        self._write_yaml('    claude_code:\n      os_model: "   "\n      worker_model: "  %s "\n' % MID)
        body = self._client().get("/v1/console/settings/engine").json()
        cfg = body["engine_models"]["claude_code"]
        self.assertIsNone(cfg["os_model"])
        self.assertEqual(cfg["worker_model"], MID)
        self.assertEqual(
            (cfg["os_model"], cfg["worker_model"]),
            (engine_models.get_tenant_engine_model("_default", "claude_code", "os_model"),
             engine_models.get_tenant_engine_model("_default", "claude_code", "worker_model")),
        )

    def test_put_rejects_unknown_claude_model(self) -> None:
        r = self._client().put("/v1/console/settings/engine", json={
            "default_engine": "claude_code",
            "engine_models": {"claude_code": {"os_model": "claude-nonexistent-99", "worker_model": None}},
        })
        self.assertEqual(r.status_code, 422, r.text)

    def test_put_accepts_catalogue_model_and_replaces_the_map(self) -> None:
        self._write_yaml('    claude_code:\n      os_model: "%s"\n      worker_model: "%s"\n      provider: null\n' % (MID, MID))
        client = self._client()
        r = client.put("/v1/console/settings/engine", json={
            "default_engine": "claude_code",
            "engine_models": {"claude_code": {"os_model": None, "worker_model": MID}},
        })
        self.assertEqual(r.status_code, 200, r.text)
        cfg = client.get("/v1/console/settings/engine").json()["engine_models"]["claude_code"]
        # REPLACE semantics: os_model was not resent → gone (the client must send the full map)
        self.assertIsNone(cfg["os_model"])
        self.assertEqual(cfg["worker_model"], MID)

    def test_put_503_when_registry_failed_to_load(self) -> None:
        with mock.patch.object(engine_models, "registry_load_status", lambda: (False, "boom")), \
             mock.patch.object(EA, "_claude_catalog_offline", lambda: set()):
            r = self._client().put("/v1/console/settings/engine", json={
                "default_engine": "claude_code",
                "engine_models": {"claude_code": {"os_model": MID, "worker_model": None}},
            })
        self.assertEqual(r.status_code, 503, r.text)

    def test_put_accepts_on_loaded_but_empty_catalogue(self) -> None:
        with mock.patch.object(engine_models, "registry_load_status", lambda: (True, None)), \
             mock.patch.object(EA, "_claude_catalog_offline", lambda: set()):
            r = self._client().put("/v1/console/settings/engine", json={
                "default_engine": "claude_code",
                "engine_models": {"claude_code": {"os_model": MID, "worker_model": None}},
            })
        self.assertEqual(r.status_code, 200, r.text)


class RegistryLoadStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (engine_models._registry_cache, engine_models._providers_cache, engine_models._load_error)

    def tearDown(self) -> None:
        engine_models._registry_cache, engine_models._providers_cache, engine_models._load_error = self._saved

    def test_failed_first_load_reports_error_then_good_load_clears_it(self) -> None:
        engine_models._registry_cache = None
        engine_models._providers_cache = None
        engine_models._load_error = None
        with mock.patch.object(engine_models, "_REGISTRY_FILE", Path("/nonexistent/registry.yaml")):
            engine_models.load_registry(force_reload=True)
            ok, err = engine_models.registry_load_status()
        self.assertFalse(ok)
        self.assertIn("Error", err or "")
        engine_models.load_registry(force_reload=True)  # real file
        self.assertEqual(engine_models.registry_load_status(), (True, None))

    def test_failure_after_good_load_serves_stale_cache_and_is_ok(self) -> None:
        engine_models.load_registry(force_reload=True)
        good = dict(engine_models._registry_cache or {})
        self.assertTrue(good)
        with mock.patch.object(engine_models, "_REGISTRY_FILE", Path("/nonexistent/registry.yaml")):
            engine_models.load_registry(force_reload=True)
            self.assertEqual(engine_models.registry_load_status(), (True, None))
            self.assertEqual(engine_models._registry_cache, good)

    def test_claude_catalog_or_503_raises_only_on_real_failure(self) -> None:
        with mock.patch.object(engine_models, "registry_load_status", lambda: (False, "x")), \
             mock.patch.object(EA, "_claude_catalog_offline", lambda: set()):
            with self.assertRaises(HTTPException) as ctx:
                EA.claude_catalog_or_503()
            self.assertEqual(ctx.exception.status_code, 503)
        with mock.patch.object(engine_models, "registry_load_status", lambda: (True, None)), \
             mock.patch.object(EA, "_claude_catalog_offline", lambda: set()):
            self.assertEqual(EA.claude_catalog_or_503(), set())


if __name__ == "__main__":
    unittest.main()
