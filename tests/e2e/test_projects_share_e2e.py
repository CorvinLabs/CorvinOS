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

    # T-0410 (replaces T-0397 AC7a): the history part ships with its scan, default on
    def test_git_history_travels_by_default_and_the_preview_counts_findings(self):
        from core.projects import bundle, bundle_rules
        with self.ctx() as c:
            pid, root = self.create(c)
            r = self.export(c, [pid])
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertNotIn("x-corvin-export-warnings", r.headers)
            z = self.tmp / "h.zip"
            z.write_bytes(r.content)
            with zipfile.ZipFile(z) as zf:
                self.assertIn(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}", zf.namelist())
            self.assertTrue(bundle.validate(z, None)["ok"])
            pv = c.own.get(f"{_X}/preview", params={"ids": pid}).json()["projects"][0]
            self.assertTrue([p for p in pv["parts"] if p["name"] == "git_history"][0]["included"])
            self.assertEqual(pv["history_findings"], 0)

    # T-0410 AC5
    def test_a_secret_in_history_is_409_and_recorded_without_a_path(self):
        with self.ctx() as c:
            pid, root = self.create(c)
            (root / "old-notes.md").write_text(f"t {JWT}\n")
            _commit_all(root, "leak")
            (root / "old-notes.md").write_text("clean\n")
            _commit_all(root, "clean")
            r = self.export(c, [pid])
            self.assertEqual(r.status_code, 409, r.text[:300])
            d = r.json()["detail"]
            self.assertEqual(d["reason_class"], "secret_in_history")
            self.assertTrue(d["message"].startswith("old-notes.md in the git history looks like it contains a secret."))
            self.assertNotIn(JWT[:12], r.text)
            refused = [self.details(e) for e in self.events(c, "project.export_refused")]
            self.assertEqual([x["reason_class"] for x in refused], ["secret_in_history"])
            self.assertNotIn("old-notes", self.chain_text(c))
            self.assertFalse([e for e in self.events(c, "project.exported") if self.details(e).get("outcome") == "applied"])
            r2 = self.export(c, [pid], parts={"git_history": False})
            self.assertEqual(r2.status_code, 200)
            pv = c.own.get(f"{_X}/preview", params={"ids": pid}).json()["projects"][0]
            self.assertEqual(pv["history_findings"], 1)

    def test_export_needs_csrf(self):
        with self.ctx() as c:
            pid, _ = self.create(c)
            self.assertEqual(c.own.post(_X, json={"project_ids": [pid]}).status_code, 403)


class ImportValidate(ShareBase):
    def upload(self, c, data: bytes, client=None, headers=None, name="bundle.zip"):
        client = client or c.own
        return client.post(_V, files={"file": (name, data, "application/zip")}, headers=headers or c.h)

    def staging_root(self, c, tenant="_default") -> Path:
        return c.home / "tenants" / tenant / "projects" / "import_staging"

    def stagings(self, c, tenant="_default") -> list[str]:
        r = self.staging_root(c, tenant)
        return sorted(p.name for p in r.iterdir()) if r.exists() else []

    def exported(self, c, pid) -> bytes:
        r = self.export(c, [pid], parts={"git_history": False})
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.content

    # AC1
    def test_a_built_bundle_answers_200_with_cards_and_the_default_location_by_precedence(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, root = self.create(c)
            data = self.exported(c, pid)
            shutil.rmtree(root)
            c.own.delete(f"{_URL}/{pid}", headers=c.h)
            registry.set_default_storage("_default", "managed")
            r = self.upload(c, data)
            self.assertEqual(r.status_code, 200, r.text[:500])
            body = r.json()
            self.assertRegex(body["staging_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(body["origin"], "unverified")
            card = body["projects"][0]
            self.assertEqual((card["id"], card["classification"]), (pid, "internal"))
            self.assertEqual(card["default_location"]["storage"], "managed")
            self.assertEqual(card["conflicts"], [])
            sd = self.staging_root(c) / body["staging_id"]
            self.assertTrue((sd / "bundle.zip").is_file() and (sd / "report.json").is_file())
            if POSIX:
                self.assertEqual(stat.S_IMODE(os.stat(sd).st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(os.stat(sd / "bundle.zip").st_mode), 0o600)
            again = c.own.get(f"{_URL}/-/import/{body['staging_id']}")
            self.assertEqual(again.status_code, 200)
            self.assertEqual(again.json()["projects"][0]["id"], pid)
            # nothing was written into the projects area
            self.assertFalse((c.root / pid).exists())
            registry.set_default_storage("_default", "root", str(c.root))
            r2 = self.upload(c, data)
            self.assertEqual(r2.json()["projects"][0]["default_location"],
                             {"storage": "root", "path": str((c.root / pid).resolve())})
            # tenant isolation: acme never sees the staging
            with mock.patch.dict(os.environ, {"CORVIN_TENANT_ID": "acme"}):
                self.assertEqual(c.other.get(f"{_URL}/-/import/{body['staging_id']}").status_code, 404)
                self.assertEqual(c.other.delete(f"{_URL}/-/import/{body['staging_id']}", headers=c.oh).status_code, 404)
            self.assertTrue(sd.exists())
            self.assertEqual(c.own.delete(f"{_URL}/-/import/{body['staging_id']}", headers=c.h).status_code, 204)
            self.assertFalse(sd.exists())
            ev = [self.details(e) for e in self.events(c, "project.import_validated")]
            self.assertEqual(len(ev), 2)
            self.assertEqual(ev[0]["outcome"], "validated")

    # AC2
    def test_every_hostile_corpus_file_answers_422_at_its_stage_and_leaves_no_staging(self):
        import zipfile as zfm
        with self.ctx() as c:
            corpus = {
                "container": dict(extra=[("projects/alpha/../../x", b"x")]),
                "container ": dict(extra=[("projects/alpha/.git/config", MARKER.encode())]),
                "envelope": dict(envelope_patch=lambda e: e.update(format_version=2)),
                "integrity": dict(extra=[("projects/alpha/extra.txt", b"x")], list_extra=False),
                "manifest": dict(extra=[("projects/alpha/sub/project.yaml",
                                         b"schema: 1\nid: inner\ntitle: x\ntenant: _default\n")]),
                "secrets": dict(extra=[("projects/alpha/t.md", JWT.encode())]),
            }
            for stage, kw in corpus.items():
                with self.subTest(stage):
                    p = make_bundle(self.tmp / "h.zip", **kw)
                    r = self.upload(c, p.read_bytes())
                    self.assertEqual(r.status_code, 422, r.text[:300])
                    self.assertEqual(r.json()["detail"]["reason_class"], stage.strip())
                    self.assertNotIn(MARKER, r.text)
                    self.assertEqual(self.stagings(c), [])
            r = self.upload(c, b"not a zip")
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (422, "container"))
            rej = [self.details(e)["reason_class"] for e in self.events(c, "project.import_rejected")]
            self.assertEqual(rej, ["container", "container", "envelope", "integrity", "manifest", "secrets", "container"])

    # AC3
    def test_conflicts_id_taken_same_source_folder_not_empty_and_nested(self):
        with self.ctx() as c:
            pid, root = self.create(c)
            data = self.exported(c, pid)
            body = self.upload(c, data).json()
            kinds = {x["kind"]: x for x in body["projects"][0]["conflicts"]}
            self.assertEqual(kinds["id_taken"]["fix"], {"suggested_id": f"{pid}-2"})
            self.assertEqual(kinds["id_taken"]["message"], f'A project "{pid}" already exists in this list.')
            self.assertEqual(kinds["same_source"]["message"],
                             "This was exported from a project in this list. It can only be imported as a copy.")
            sid = body["staging_id"]
            # folder_not_empty at the default place of a fresh id
            hand = make_bundle(self.tmp / "o.zip", {"omega": {"a.txt": b"x"}})
            b2 = self.upload(c, hand.read_bytes()).json()
            dl = Path(b2["projects"][0]["default_location"]["path"])
            dl.mkdir(parents=True)
            (dl / "x.txt").write_text("x")
            b3 = self.upload(c, hand.read_bytes()).json()
            self.assertEqual([x["kind"] for x in b3["projects"][0]["conflicts"]], ["folder_not_empty"])
            self.assertEqual(b3["projects"][0]["conflicts"][0]["message"], "That folder already exists and is not empty.")
            # nested via /verdict
            v = c.own.get(f"{_URL}/-/import/{sid}/verdict",
                          params={"project": pid, "location": str(root / "inner"), "project_id": "fresh-id"})
            self.assertEqual(v.status_code, 200, v.text)
            kinds = [x["kind"] for x in v.json()["conflicts"]]
            self.assertIn("nested", kinds)
            self.assertNotIn("id_taken", kinds)
            self.assertEqual([x for x in v.json()["conflicts"] if x["kind"] == "nested"][0]["message"],
                             f"It lies inside another project ({pid}).")

    # AC4
    def test_staging_cap_ttl_sweeps_and_the_host_budget(self):
        with self.ctx() as c:
            data = make_bundle(self.tmp / "s.zip").read_bytes()
            ids = [self.upload(c, data).json()["staging_id"] for _ in range(6)]
            have = self.stagings(c)
            self.assertEqual(len(have), 5)
            self.assertNotIn(ids[0], have)
            # TTL: one projector tick an hour later
            later = time.time() + 3601
            out = c.svc.sweep_share("_default", now=later)
            self.assertEqual(out["stagings"], 5)
            self.assertEqual(self.stagings(c), [])
            # TTL: the next validate request sweeps too
            sid = self.upload(c, data).json()["staging_id"]
            real = time.time
            with mock.patch.object(c.svc, "_now", lambda: real() + 3601):
                nxt = self.upload(c, data).json()["staging_id"]
            self.assertEqual(self.stagings(c), [nxt])
            self.assertNotIn(sid, self.stagings(c))
            # host budget of three stagings: another tenant is refused and nothing of this tenant is dropped
            self.upload(c, data)
            self.upload(c, data)
            mine = self.stagings(c)
            self.assertEqual(len(mine), 3)
            size = (self.staging_root(c) / mine[0] / "bundle.zip").stat().st_size
            with mock.patch.object(c.svc, "IMPORT_HOST_BUDGET_BYTES", 3 * size + 10), \
                    mock.patch.dict(os.environ, {"CORVIN_TENANT_ID": "acme"}):
                r = self.upload(c, data, client=c.other, headers=c.oh)
            self.assertEqual(r.status_code, 429, r.text[:300])
            self.assertEqual(r.json()["detail"], {"message": "Too many imports are waiting; finish or cancel one first.",
                                                  "reason_class": "staging_full"})
            self.assertEqual(self.stagings(c), mine)
            self.assertEqual(self.stagings(c, "acme"), [])
            rej = [self.details(e) for e in self.events(c, "project.import_rejected", tenant="acme")]
            self.assertEqual([x["reason_class"] for x in rej], ["staging_full"])
            text = self.chain_text(c, "acme")
            self.assertNotIn("bundle.zip", text)
            self.assertNotIn(str(c.home), text)

    # AC5
    def test_a_501_mib_upload_is_413_before_the_body_is_read_and_never_above_one_chunk_past_the_limit(self):
        with self.ctx() as c:
            seen = {"max": 0}
            real_write = c.svc.staging_write

            def spy(handle, data):
                real_write(handle, data)
                seen["max"] = max(seen["max"], handle.written)
            boundary = "corvinboundary7"
            head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"b.zip\"\r\n"
                    "Content-Type: application/zip\r\n\r\n").encode()
            sent = {"n": 0}

            async def body():
                yield head
                chunk = b"\0" * (1 << 20)
                for _ in range(501):
                    sent["n"] += len(chunk)
                    yield chunk
                yield f"\r\n--{boundary}--\r\n".encode()

            async def run():
                async with self.client(c, c.own) as ac:
                    return await ac.post(_V, content=body(), headers={**c.h, "Content-Type": f"multipart/form-data; boundary={boundary}"})
            with mock.patch.object(c.svc, "staging_write", spy):
                r = asyncio.run(run())
            self.assertEqual(r.status_code, 413, r.text[:300])
            self.assertEqual(r.json()["detail"]["message"], "The file is larger than 500 MB.")
            self.assertLessEqual(seen["max"], (500 << 20) + (1 << 20))
            self.assertGreater(seen["max"], 499 << 20)                              # positive control: it streamed
            self.assertEqual(self.stagings(c), [])
            # a declared length above the limit is refused before anything is read
            r = c.own.post(_V, content=b"x", headers={**c.h, "Content-Type": f"multipart/form-data; boundary={boundary}",
                                                      "Content-Length": str((502 << 20))})
            self.assertEqual(r.status_code, 413)

    # AC6
    def test_both_events_in_both_registries_and_no_path_or_file_name_in_the_chain(self):
        from forge import security_events as se
        with self.ctx() as c:
            for ev in ("project.import_validated", "project.import_rejected"):
                self.assertIn(ev, se.EVENT_SEVERITY)
                self.assertIn(ev, se._EVENT_ALLOWLIST)
            data = make_bundle(self.tmp / "q.zip").read_bytes()
            self.assertEqual(self.upload(c, data, name="very-unique-upload-name.zip").status_code, 200)
            self.assertEqual(self.upload(c, b"junk", name="very-unique-upload-name.zip").status_code, 422)
            text = self.chain_text(c)
            self.assertNotIn("very-unique-upload-name", text)
            self.assertNotIn(str(c.home), text)
            ok = [self.details(e) for e in self.events(c, "project.import_validated")]
            self.assertEqual(ok[0]["files"], 2)
            self.assertNotIn("_dropped_fields", json.dumps(ok))

    def test_import_validate_needs_csrf(self):
        with self.ctx() as c:
            r = c.own.post(_V, files={"file": ("b.zip", b"x", "application/zip")})
            self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
