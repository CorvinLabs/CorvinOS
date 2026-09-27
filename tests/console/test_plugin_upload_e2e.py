"""Plugin upload (ADR-2085) + Skill Manager (ADR-0681) through the real console router.

The previous version of this file never collected (``pytest_plugins = ["conftest"]``
in a non-root conftest, and fixtures ``client`` / ``admin_session`` that exist
nowhere), so the upload routes shipped with no proof at all — and with an
import (``corvin_console.audit.emit_audit``) that did not exist, which made
``corvin_console.app`` fail to import and took the WHOLE console down.

What must hold now (real HTTP through ``_sandbox``, a real session + CSRF):

* upload / approve / reject need a session (401) and the CSRF token (403);
* the routes are served at ``/v1/console/plugin-uploads`` (not the doubled
  ``/v1/console/v1/skills/...``);
* a client file name like ``../../x.zip`` never becomes a path outside the
  tenant's staging directory;
* an invalid package (path-traversal entry) is refused with 400 and never staged,
  so it can never be approved;
* approve of an unknown id is a 404, not a 500;
* audit records land in the tenant chain;
* Skill Manager install/uninstall need a session + CSRF (they had NO auth: a
  multipart POST from any web page could install a skill).
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_CONSOLE_TESTS = Path(__file__).resolve().parents[2] / "core" / "console" / "tests"
sys.path.insert(0, str(_CONSOLE_TESTS))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

BASE = "/v1/console/plugin-uploads"


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _valid_zip(name: str = "test-skill") -> bytes:
    return _zip({
        "manifest.json": json.dumps({"name": name, "version": "1.0.0", "author": "t"}),
        "src/main.py": "print('hello')",
    })


class PluginUploadTest(unittest.TestCase):
    def test_auth_and_csrf_required(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            files = {"file": ("a.zip", _valid_zip(), "application/zip")}
            self.assertEqual(client.post(BASE, files=files).status_code, 403)
            client.cookies.clear()
            self.assertEqual(client.post(BASE, files=files).status_code, 401)
            self.assertEqual(client.get(BASE).status_code, 401)
            self.assertEqual(client.post(f"{BASE}/0123456789abcdef/approve").status_code, 401)

    def test_doubled_prefix_is_gone(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            r = client.post("/v1/console/v1/skills/upload", headers={"X-CSRF-Token": csrf},
                            files={"file": ("a.zip", _valid_zip(), "application/zip")})
            self.assertEqual(r.status_code, 404)

    def test_upload_list_approve_and_audit(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            h = {"X-CSRF-Token": csrf}
            r = client.post(BASE, headers=h,
                            files={"file": ("../../../evil.zip", _valid_zip(), "application/zip")})
            self.assertEqual(r.status_code, 200, r.text)
            upload_id = r.json()["upload_id"]
            self.assertNotIn("/", r.json()["file_name"])
            # Nothing was written outside the staging dir.
            tenant_global = home / "tenants" / "_default" / "global"
            self.assertEqual([p for p in home.parent.rglob("*evil*") if "plugin_staging" not in p.parts], [])
            staged = list((tenant_global / "plugin_staging").iterdir())
            self.assertTrue(any(p.name == f"{upload_id}.zip" for p in staged), staged)

            listed = client.get(BASE).json()
            self.assertEqual(listed["count"], 1)
            self.assertEqual(client.get(f"{BASE}/{upload_id}/manifest").json()["manifest"]["name"],
                             "test-skill")

            r = client.post(f"{BASE}/{upload_id}/approve", headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("installed_path", r.json())
            self.assertTrue((tenant_global / "plugins_installed" / f"{upload_id}.zip").exists())

            self.assertIn("plugin.upload_staged", repr(_audit_events(home)))
            self.assertIn("plugin.upload_approved", repr(_audit_events(home)))

    def test_invalid_package_is_refused_not_staged(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            bad = _zip({
                "manifest.json": json.dumps({"name": "x", "version": "1", "author": "a"}),
                "../escape.py": "boom",
            })
            r = client.post(BASE, headers={"X-CSRF-Token": csrf},
                            files={"file": ("bad.zip", bad, "application/zip")})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(client.get(BASE).json()["count"], 0)
            staging = home / "tenants" / "_default" / "global" / "plugin_staging"
            self.assertEqual([p.name for p in staging.iterdir()], [])

    def test_unknown_or_malformed_id(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            h = {"X-CSRF-Token": csrf}
            self.assertEqual(client.post(f"{BASE}/0123456789abcdef/approve", headers=h).status_code, 404)
            self.assertEqual(client.post(f"{BASE}/0123456789abcdef/reject", headers=h).status_code, 404)
            self.assertEqual(client.get(f"{BASE}/not-an-id/manifest").status_code, 400)

    def test_tenant_isolation(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("_default", "tenant-b")) as (
            client, csrf, _home, clients,
        ):
            r = client.post(BASE, headers={"X-CSRF-Token": csrf},
                            files={"file": ("a.zip", _valid_zip(), "application/zip")})
            upload_id = r.json()["upload_id"]
            client_b, csrf_b = clients["tenant-b"]
            self.assertEqual(client_b.get(BASE).json()["count"], 0)
            r = client_b.post(f"{BASE}/{upload_id}/approve", headers={"X-CSRF-Token": csrf_b})
            self.assertEqual(r.status_code, 404)


class SkillManagerAuthTest(unittest.TestCase):
    def test_install_and_uninstall_require_session_and_csrf(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            base = "/v1/console/skills-manager/skills"
            files = {"file": ("s.zip", _valid_zip(), "application/zip")}
            form = {"skill_id": "demo", "version": "1.0.0"}
            self.assertEqual(client.post(f"{base}/install", files=files, data=form).status_code, 403)
            self.assertEqual(client.delete(f"{base}/uninstall/demo/1.0.0").status_code, 403)

            r = client.post(f"{base}/install", files=files, data=form, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            # Installed under CORVIN_HOME, never the operator's ~/.corvin.
            self.assertTrue((home / "skills_installed" / "demo" / "1.0.0").is_dir())
            self.assertEqual(client.get(f"{base}/installed").json()["total"], 1)
            self.assertNotIn("registry_path", client.get(f"{base}/health").json())

            r = client.delete(f"{base}/uninstall/demo/1.0.0", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("skill.uninstall", repr(_audit_events(home)))

            client.cookies.clear()
            self.assertEqual(client.get(f"{base}/installed").status_code, 401)
            self.assertEqual(client.post(f"{base}/install", files=files, data=form).status_code, 401)


if __name__ == "__main__":
    unittest.main()
