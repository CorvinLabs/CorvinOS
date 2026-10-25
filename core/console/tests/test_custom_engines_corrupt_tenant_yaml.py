"""PUT /engines/custom/{id} fails closed on an unparseable tenant.corvin.yaml (T-0227).

The route reads spec.data_residency from the tenant file to refuse a us_cloud engine in
an EU zone. The reader used to return {} for a corrupt file, which skipped that check
and let the engine register. A corrupt file must now refuse the request (422) and write
no manifest; a valid EU file still refuses us_cloud; a missing file still registers.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import custom_engines as CE  # noqa: E402

CORRUPT = b"spec:\n  data_residency:\n    zone: eu\n    allowed_engines: [claude_code\n  : : :\n"
EU_ONLY = b"spec:\n  data_residency:\n    zone: eu\n    allowed_engines: [claude_code]\n"


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


def _body(locality: str) -> dict:
    return {
        "display_name": "Test engine",
        "transport": "openai_compat",
        "base_url": "http://127.0.0.1:9/v1",
        "locality": locality,
        "network_egress": "none",
        "data_classification": "PUBLIC",
    }


class CustomEngineCorruptTenantYamlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.yaml_path = CE._forge_paths.tenant_global_dir("_default") / "tenant.corvin.yaml"
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest = CE._engine_path("_default", "demo")

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(CE.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: _fake_record()
        return TestClient(app)

    def test_corrupt_file_refuses_and_writes_no_manifest(self) -> None:
        self.yaml_path.write_bytes(CORRUPT)
        r = self._client().put("/engines/custom/demo", json=_body("us_cloud"))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertFalse(self.manifest.exists(), "an engine registered despite a corrupt tenant file")
        self.assertEqual(self.yaml_path.read_bytes(), CORRUPT)

    def test_valid_eu_file_still_refuses_us_cloud(self) -> None:
        self.yaml_path.write_bytes(EU_ONLY)
        r = self._client().put("/engines/custom/demo", json=_body("us_cloud"))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertFalse(self.manifest.exists())

    def test_missing_file_still_registers_a_local_engine(self) -> None:
        r = self._client().put("/engines/custom/demo", json=_body("local"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.manifest.exists())


if __name__ == "__main__":
    unittest.main()
