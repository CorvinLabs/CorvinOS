"""PUT /settings/engine must never overwrite an unparseable tenant.corvin.yaml (T-0227).

Before the fix the reader returned {} for a corrupt file, so the writer replaced the
whole file with its one section and dropped the L34 data-residency policy. Now the
writer refuses with 422 and the file stays byte-identical. The same file makes the
custom-engine reader fail closed instead of skipping the data-residency check.
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
from corvin_console.routes import engine as ENG  # noqa: E402

CORRUPT = b"spec:\n  data_residency:\n    zone: eu\n    allowed_engines: [claude_code\n  : : :\n"


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


class CorruptTenantYamlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name
        self.path = ENG._tenant_yaml_path("_default")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(CORRUPT)

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ENG.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        return TestClient(app)

    def test_put_refuses_and_leaves_corrupt_file_byte_identical(self) -> None:
        r = self._client().put(
            "/settings/engine",
            json={
                "default_engine": "claude_code",
                "engine_models": {"claude_code": {"os_model": "claude-opus-5", "provider": "anthropic"}},
            },
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.path.read_bytes(), CORRUPT, "the corrupt file was rewritten")

    def test_put_still_writes_when_file_is_missing(self) -> None:
        self.path.unlink()
        r = self._client().put(
            "/settings/engine",
            json={"default_engine": "claude_code", "engine_models": None},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
