"""PLAN-0972 phases 3-4 / ADR-2323 - sharing projects over real HTTP: export (T-0397, T-0410) and import validate (T-0399).

The console router with real sessions for two tenants, a real filesystem, real git, real gpg when installed and the real
audit chain of the sandbox (``CORVIN_HOME`` in the test's temp dir, HOME and XDG_DATA_HOME there too). Concurrent requests run
in ONE event loop (httpx over the ASGI app), so "the loop stays free" is measured. The process tenant is ``_default``; a
request of ``acme`` runs with ``CORVIN_TENANT_ID=acme`` so its audit records land in its own chain, as on its own host.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
sys.path.insert(0, str(HERE.parents[1]))
from _projects_fixtures import sandbox_home_env, small_catalogue, write_template  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

from tests.projects.test_project_bundle import JWT, MARKER, _commit_all, _gitc, make_bundle  # noqa: E402

_URL = "/v1/console/projects"
_X = f"{_URL}/-/export"
_V = f"{_URL}/-/import/validate"
POSIX = os.name != "nt"
GPG = shutil.which("gpg")


class ShareBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = small_catalogue(self.tmp / "src")
        write_template(self.src, "plain", ["core"])
        self._env = sandbox_home_env(self.tmp)
        self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @contextlib.contextmanager
    def ctx(self):
        outer = self
        with _sandbox(self.tmp / "s", tenants=("_default", "acme")) as (_c, _t, home, clients):
            from core.projects import registry
            from corvin_console import projects_service as svc
            root = outer.tmp / "projects"
            root.mkdir(exist_ok=True)
            registry.set_projects_root("_default", str(root))
            registry.set_projects_root("acme", str(root / "acme"))
            with mock.patch.object(svc, "template_source", lambda tenant: outer.src), \
                    mock.patch.object(svc, "plugin_state", lambda tenant, pid: "enabled"):
                class C:
                    pass
                c = C()
                c.home, c.root, c.svc, c.app = home, root, svc, clients["_default"][0].app
                c.own, c.csrf = clients["_default"]
                c.other, c.other_csrf = clients["acme"]
                c.h = {"X-CSRF-Token": c.csrf}
                c.oh = {"X-CSRF-Token": c.other_csrf}
                assert c.own.get(_URL).status_code == 200            # warm-up (one-time app setup) + workspace binding
                yield c

    def create(self, c, title="Alpha", commit=True) -> tuple[str, Path]:
        plan = c.own.post(f"{_URL}/plan", json={"template": "plain", "title": title, "storage": "root"}, headers=c.h)
        self.assertEqual(plan.status_code, 200, plan.text)
        r = c.own.post(_URL, json={"plan_id": plan.json()["plan_id"]}, headers=c.h)
        self.assertEqual(r.status_code, 201, r.text)
        root = Path(plan.json()["location"])
        if commit:
            _commit_all(root, "initial")
        return r.json()["project"]["id"], root

    def export(self, c, ids, client=None, headers=None, **body):
        client = client or c.own
        return client.post(_X, json={"project_ids": list(ids), **body}, headers=headers or c.h)

    def events(self, c, name, tenant="_default"):
        return [e for e in _audit_events(c.home, tenant) if (e.get("event_type") or e.get("event")) == name]

    def chain_text(self, c, tenant="_default") -> str:
        f = c.home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def exports_dir(self, c, tenant="_default") -> Path:
        return c.home / "tenants" / tenant / "projects" / "exports"

    @staticmethod
    def details(e: dict) -> dict:
        return e.get("details") or e

    def client(self, c, cookies_from):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app), base_url="http://127.0.0.1",
                                 cookies={"corvin_console_sid": cookies_from.cookies.get("corvin_console_sid")}, timeout=600)


class Export(ShareBase):
    # AC1
    def test_internal_project_exports_200_with_an_envelope_that_validates_and_equals_the_preview(self):
        from core.projects import bundle
        with self.ctx() as c:
            pid, root = self.create(c)
            (root / "notes.md").write_text("hello\n")
            pv = c.own.get(f"{_X}/preview", params={"ids": pid, "parts": "kb"})
            self.assertEqual(pv.status_code, 200, pv.text)
            self.assertIn("gpg", pv.json())
            r = self.export(c, [pid], parts={"git_history": False})
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertEqual(r.headers["content-type"], "application/zip")
            self.assertRegex(r.headers["content-disposition"], rf'attachment; filename="?corvin-project-{pid}-\d{{8}}\.zip')
            z = self.tmp / "dl.zip"
            z.write_bytes(r.content)
            rep = bundle.validate(z, None)
            self.assertTrue(rep["ok"], rep)
            with zipfile.ZipFile(z) as zf:
                env = json.loads(zf.read(bundle.ENVELOPE_NAME))
            self.assertEqual(env["projects"][0]["counts"], pv.json()["projects"][0]["counts"])
            self.assertEqual(list(self.exports_dir(c).iterdir()) if self.exports_dir(c).exists() else [], [])

    # AC2
    def test_secret_refused_confidential_needs_confirmation(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, _ = self.create(c)
            registry.set_protection("_default", pid, "secret", "read")
            r = self.export(c, [pid])
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json()["detail"], {"message": "Secret projects cannot be exported.", "reason_class": "classification"})
            registry.set_protection("_default", pid, "confidential", "read")
            r = self.export(c, [pid])
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json()["detail"]["reason_class"], "confirm_required")
            self.assertEqual(r.json()["detail"]["message"], "This project is confidential. Confirm that you want to export it.")
            r = self.export(c, [pid], confirm=True)
            self.assertEqual(r.status_code, 200, r.text[:300])
            refused = [self.details(e) for e in self.events(c, "project.export_refused")]
            self.assertEqual([x["reason_class"] for x in refused], ["classification", "confirm_required"])

    # AC3
    def test_planted_token_409_secret_shape_naming_the_path_and_no_file_left(self):
        with self.ctx() as c:
            pid, root = self.create(c)
            (root / "creds.md").write_text(f"x {JWT}\n")
            r = self.export(c, [pid])
            self.assertEqual(r.status_code, 409)
            d = r.json()["detail"]
            self.assertEqual(d["reason_class"], "secret_shape")
            self.assertEqual(d["message"], "creds.md looks like it contains a secret. Exclude or clean it, then export again.")
            self.assertNotIn(JWT[:12], r.text)
            leftovers = [p for p in self.exports_dir(c).rglob("*")] if self.exports_dir(c).exists() else []
            self.assertEqual(leftovers, [])
            self.assertFalse([e for e in self.events(c, "project.exported") if self.details(e).get("outcome") == "applied"])

    # AC4 + AC5 + AC8
    def test_large_exports_go_202_two_slots_third_429_at_once_and_the_loop_stays_free(self):
        with self.ctx() as c:
            ids = []
            for n in ("Big One", "Big Two", "Big Three"):
                pid, root = self.create(c, n, commit=False)
                with open(root / "blob.bin", "wb") as f:
                    f.truncate(150 << 20)                              # sparse
                ids.append(pid)

            async def run():
                async with self.client(c, c.own) as ac:
                    async def post(pid):
                        t = time.monotonic()
                        r = await ac.post(_X, json={"project_ids": [pid], "parts": {"git_history": False}}, headers=c.h)
                        return r, time.monotonic() - t
                    tasks = [asyncio.create_task(post(p)) for p in ids[:2]]
                    await asyncio.sleep(0.5)
                    third = await post(ids[2])
                    t = time.monotonic()
                    lst = await ac.get(_URL)
                    list_s = time.monotonic() - t
                    first = await asyncio.gather(*tasks)
                    return first, third, lst, list_s
            first, third, lst, list_s = asyncio.run(run())
            for r, _s in first:
                self.assertEqual(r.status_code, 202, r.text[:300])
            self.assertEqual(third[0].status_code, 429, third[0].text[:300])
            self.assertEqual(third[0].json()["detail"]["reason_class"], "busy")
            self.assertLess(third[1], 1.0)
            self.assertEqual(lst.status_code, 200)
            self.assertLess(list_s, 1.0)
            job = first[0][0].json()["job_id"]
            deadline = time.monotonic() + 300
            while True:
                st = c.own.get(f"{_URL}/-/exports/{job}").json()
                if st["state"] != "running" or time.monotonic() > deadline:
                    break
                time.sleep(0.3)
            self.assertEqual(st["state"], "done", st)
            dl = c.own.get(st["download_url"])
            self.assertEqual(dl.status_code, 200)
            self.assertGreater(len(dl.content), 150 << 20)
            jd = self.exports_dir(c) / job
            if POSIX:
                self.assertEqual(stat.S_IMODE(os.stat(jd).st_mode), 0o700)
                for f in jd.iterdir():
                    self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600, f.name)
            # another tenant never sees the job
            self.assertEqual(c.other.get(f"{_URL}/-/exports/{job}").status_code, 404)

    # AC6
    @unittest.skipUnless(GPG, "gpg is not installed on this host")
    def test_passphrase_yields_a_gpg_file_that_decrypts_and_no_plain_zip_remains(self):
        with self.ctx() as c:
            pid, root = self.create(c)
            r = self.export(c, [pid], passphrase="correct horse battery", parts={"git_history": False})
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertEqual(r.headers["content-type"], "application/pgp-encrypted")
            self.assertIn(".zip.gpg", r.headers["content-disposition"])
            enc = self.tmp / "x.zip.gpg"
            enc.write_bytes(r.content)
            gh = self.tmp / "gnupg"
            gh.mkdir(mode=0o700)
            out = subprocess.run([GPG, "--homedir", str(gh), "--batch", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                                  "--decrypt", str(enc)], input=b"correct horse battery", capture_output=True)
            self.assertEqual(out.returncode, 0, out.stderr[-500:])
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(out.stdout)))
            self.assertFalse(list(self.exports_dir(c).rglob("*.zip")) if self.exports_dir(c).exists() else [])

    def test_gpg_missing_is_409(self):
        with self.ctx() as c:
            pid, _ = self.create(c)
            with mock.patch.object(c.svc.shutil, "which", lambda name, *a, **k: None if name == "gpg" else shutil.which(name)):
                r = self.export(c, [pid], passphrase="correct horse battery")
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json()["detail"]["reason_class"], "gpg_missing")

    # AC7
    def test_audit_events_registered_ordered_and_without_paths_or_titles(self):
        from forge import security_events as se
        with self.ctx() as c:
            for ev in ("project.exported", "project.export_refused"):
                self.assertIn(ev, se.EVENT_SEVERITY)
                self.assertIn(ev, se._EVENT_ALLOWLIST)
            pid, root = self.create(c, "Very Distinct Title Xq")
            r = self.export(c, [pid], parts={"git_history": False})
            self.assertEqual(r.status_code, 200)
            ex = [self.details(e) for e in self.events(c, "project.exported")]
            self.assertEqual([x["outcome"] for x in ex], ["requested", "applied"])
            self.assertEqual(ex[1]["project_id"], pid)
            self.assertIn("files", ex[1]["parts"].split(","))
            self.assertNotIn("_dropped_fields", json.dumps(ex))
            text = self.chain_text(c)
            for bad in (str(root), str(c.root), "Very Distinct Title Xq", str(self.tmp)):
                self.assertNotIn(bad, text)

    def test_another_tenant_cannot_export_this_tenants_project(self):
        with self.ctx() as c:
            pid, _ = self.create(c)
            with mock.patch.dict(os.environ, {"CORVIN_TENANT_ID": "acme"}):
                r = self.export(c, [pid], client=c.other, headers=c.oh)
            self.assertEqual(r.status_code, 404, r.text[:200])

    # AC7a (N11) - replaced by T-0410's history tests once the scan lands
    def test_git_history_is_not_available_yet_and_says_so(self):
        from core.projects import bundle
        with self.ctx() as c:
            pid, root = self.create(c)
            r = self.export(c, [pid], parts={"git_history": True})
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertIn("Git history export is not available yet.", r.headers.get("x-corvin-export-warnings", ""))
            z = self.tmp / "h.zip"
            z.write_bytes(r.content)
            with zipfile.ZipFile(z) as zf:
                self.assertFalse([n for n in zf.namelist() if ".corvin-export" in n])
                env = json.loads(zf.read(bundle.ENVELOPE_NAME))
            part = [p for p in env["projects"][0]["parts"] if p["name"] == "git_history"][0]
            self.assertFalse(part["included"])
            pv = c.own.get(f"{_X}/preview", params={"ids": pid}).json()
            self.assertEqual([p for p in pv["projects"][0]["parts"] if p["name"] == "git_history"][0],
                             {"name": "git_history", "included": False, "note": "unavailable"})

    def test_export_needs_csrf(self):
        with self.ctx() as c:
            pid, _ = self.create(c)
            self.assertEqual(c.own.post(_X, json={"project_ids": [pid]}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
