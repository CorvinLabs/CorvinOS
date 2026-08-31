"""HTTP-level security contract for the console file API (2026-07-30 review).

The existing test_files_access_control.py calls `_access()` directly. Finding F6
of the 2026-07-30 review was that the gate is a correct function wired WRONG at
some call sites — a class of bug a unit test on the function can never see. This
suite drives the real FastAPI routes through TestClient:

  * C4: /files/upload only graded the destination DIRECTORY, never the final
    filename (delete/mkdir graded the full path). An upload of "secrets.enc"
    into the writable "global/" dir overwrote the tenant's encrypted secrets.
  * F4: a symlink inside the tenant tree pointing at key material passed
    _resolve_safe's containment check and was graded on its harmless link name,
    so /files/download served the secret. _effective_access must block it.

Run: python3 core/console/tests/test_files_http_security.py
"""
from __future__ import annotations

import dataclasses
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import files as F  # noqa: E402


def _fake_record() -> session_auth.SessionRecord:
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
            values[f.name] = "_default"
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class FilesHttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name) / "tenant"
        (self.root / "global").mkdir(parents=True)
        (self.root / "files").mkdir(parents=True)

        # Point the route's tenant_home at our tmp root, regardless of tenant id.
        self.th = patch.object(F._forge_paths, "tenant_home", lambda tid: self.root)
        self.th.start(); self.addCleanup(self.th.stop)
        self.audit = patch.object(F, "console_audit")
        self.audit.start(); self.addCleanup(self.audit.stop)

        app = FastAPI()
        app.include_router(F.router)
        rec = _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: rec
        app.dependency_overrides[console_deps.require_session] = lambda: rec
        self.client = TestClient(app)

    def _upload(self, dir_, name, content=b"x"):
        return self.client.post(
            "/files/upload",
            params={"dir": dir_},
            files={"file": (name, io.BytesIO(content), "application/octet-stream")},
        )

    # ── C4: upload must grade the final filename, not just the directory ──

    def test_upload_cannot_overwrite_secrets_enc(self):
        secret = self.root / "global" / "secrets.enc"
        secret.write_bytes(b"ORIGINAL-ENCRYPTED-SECRETS")
        r = self._upload("global", "secrets.enc", b"OVERWRITTEN-BY-UPLOAD")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(secret.read_bytes(), b"ORIGINAL-ENCRYPTED-SECRETS",
                         "secrets.enc must be untouched by a rejected upload")

    def test_upload_cannot_write_byok_key(self):
        r = self._upload("global/agent", "byok_privkey.pem", b"-----FAKE KEY-----")
        self.assertEqual(r.status_code, 403, r.text)

    def test_upload_ordinary_file_still_works(self):
        r = self._upload("files", "notes.txt", b"hello")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.root / "files" / "notes.txt").read_bytes(), b"hello")

    # ── F4: a symlink to a secret must not be downloadable ──

    def test_download_through_symlink_to_secret_is_blocked(self):
        keys = self.root / "keys"
        keys.mkdir()
        (keys / "tenant_master.key").write_text("MASTER-KEY")
        os.symlink(keys / "tenant_master.key", self.root / "files" / "leak")
        r = self.client.get("/files/download", params={"path": "files/leak"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertNotIn("MASTER-KEY", r.text)

    def test_download_ordinary_file_works(self):
        (self.root / "files" / "ok.txt").write_text("readable")
        r = self.client.get("/files/download", params={"path": "files/ok.txt"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("readable", r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
