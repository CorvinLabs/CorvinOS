"""P-E — pipeline editor route (ADR-0284).

Drives GET/PUT /vibe-engineering/pipeline through the real router:
  * GET returns current config + palette.
  * PUT rejects unknown ids, a missing memory root, and a broken requires-DAG.
  * PUT persists a valid reorder.

Run: python3 core/console/tests/test_pipeline_editor_adr0284.py
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import vibe_engineering as V  # noqa: E402


def _rec(tenant_id="_default"):
    now = 1_000_000.0
    vals = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            vals[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            vals[f.name] = False
        elif f.name == "tier":
            t = getattr(session_auth, "Tier", None)
            vals[f.name] = next(iter(t)) if t else "owner"
        elif f.name == "tenant_id":
            vals[f.name] = tenant_id
        else:
            vals[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**vals)  # type: ignore[arg-type]


@unittest.skipIf(V._CEL_STAGES is None, "CEL stages not loadable in this env")
class PipelineEditorTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name)
        self.pp = patch.object(V._forge_paths, "tenant_global_dir",
                               lambda tid: self.base / tid / "global")
        self.pp.start(); self.addCleanup(self.pp.stop)

    def _client(self):
        app = FastAPI()
        app.include_router(V.router)
        rec = _rec()
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
        return TestClient(app)

    def test_get_returns_palette(self):
        body = self._client().get("/vibe-engineering/pipeline").json()
        self.assertTrue(body["available"])
        pal = {p["id"] for p in body["palette"]}
        self.assertIn("memory", pal)
        self.assertIn("llm_synthesis", pal)

    def test_put_rejects_unknown(self):
        r = self._client().put("/vibe-engineering/pipeline",
                               json={"pipeline": [{"stage": "memory"}, {"stage": "nope"}]})
        self.assertEqual(r.status_code, 400)

    def test_put_rejects_missing_memory_root(self):
        r = self._client().put("/vibe-engineering/pipeline",
                               json={"pipeline": [{"stage": "graph"}]})
        self.assertEqual(r.status_code, 400)

    def test_put_rejects_broken_requires(self):
        # skill requires graph — omit graph
        r = self._client().put("/vibe-engineering/pipeline",
                               json={"pipeline": [{"stage": "memory"}, {"stage": "skill"}]})
        self.assertEqual(r.status_code, 400)

    def test_put_persists_valid(self):
        r = self._client().put(
            "/vibe-engineering/pipeline",
            json={"pipeline": [{"stage": "memory"}, {"stage": "graph"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        # written to tenant.corvin.yaml
        p = self.base / "_default" / "global" / "tenant.corvin.yaml"
        self.assertTrue(p.exists())
        self.assertIn("context_engineering", p.read_text())


if __name__ == "__main__":
    unittest.main()
