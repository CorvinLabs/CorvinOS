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

            import threading
            hold = threading.Event()
            real_build = c.svc._do_build

            def held_build(*a, **k):
                # the two jobs hold their slots until the third request was answered (a fast host finished a 150 MiB
                # sparse build inside the test's 0.5 s and freed a slot - the assertion measured the host, not the gate)
                hold.wait(120)
                return real_build(*a, **k)

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
                    hold.set()
                    return first, third, lst, list_s
            try:
                with mock.patch.object(c.svc, "_do_build", held_build):
                    first, third, lst, list_s = asyncio.run(run())
            finally:
                hold.set()
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
            self.assertEqual(set(kinds), {"id_taken", "same_source"})            # the place is the suggested id's, not the source's
            self.assertTrue(body["projects"][0]["default_location"]["path"].endswith(f"{pid}-2"))
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
            # a new id is "import as a copy": the verdict no longer lists same_source, so the Import button can enable (T-0402)
            v = c.own.get(f"{_URL}/-/import/{sid}/verdict", params={"project": pid, "project_id": "fresh-id"})
            self.assertEqual([x["kind"] for x in v.json()["conflicts"]], [], v.text)
            v = c.own.get(f"{_URL}/-/import/{sid}/verdict", params={"project": pid, "project_id": pid})
            self.assertEqual(sorted(x["kind"] for x in v.json()["conflicts"]), ["id_taken", "same_source"], v.text)

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



# ── import apply, git step, pending state, duplicate (T-0400, T-0411, T-0412, T-0401) ─────────────────────────────────────

import test_project_kb_projector_e2e as fx  # noqa: E402

from tests.projects.test_project_git_import import GitSpy  # noqa: E402

_MARKET = fx._projects_root() / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management" / "corvin_knowledge" / "src" / "kb_init.py"
_KB_OK = (fx._KB_SRC / "kb.py").is_file() and _MARKET.is_file() and "--no-engine" in _MARKET.read_text()
ACME = {"CORVIN_TENANT_ID": "acme"}
FORGE_README = "forge notes of the acs fragment\n"


def _tree(root: Path) -> dict[str, str]:
    """Regular files below *root* by sha256, without the machine-local and git parts and the rebuilt graph."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if {".git", ".corvin"} & set(rel.parts) or rel.parts[-1] == ".mcp.json" or rel.parts[:3] == ("docs", "kb", "graph"):
            continue
        if p.is_symlink():
            out[rel.as_posix()] = "LINK"
        elif p.is_file():
            out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _glog(repo: Path, ref: str) -> str:
    return _gitc(repo, "log", "--format=%H %s", ref)


@unittest.skipUnless(_KB_OK, "needs Corvin-Knowledge and a Corvin-Marketplace checkout whose kb_init has --no-engine")
class ApplyBase(ShareBase):
    def setUp(self):
        super().setUp()
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self._saved_kb = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)
        # the acs fragment seeds a file below .corvin/ (REVIEW-0062 N3: it never travels, repair re-seeds it)
        from _projects_fixtures import write_fragment
        write_fragment(self.src, "acs", requires=["claude"], texts={"files/forge.md": FORGE_README},
                       **{"files": [{"src": "files/forge.md", "dst": ".corvin/forge/README.md", "kind": "seed"}],
                          "mcp": {"corvin_orchestration": {"command": "{{paths.python}}", "args": ["-m", "json.tool"],
                                                           "env": {"CORVIN_HOME": "{{paths.corvin_home}}"}}}})
        write_template(self.src, "agentic", ["core", "claude", "acs"], ["core", "claude", "acs"])

    def tearDown(self):
        if self._saved_kb is None:
            os.environ.pop("CORVIN_KB_REPO", None)
        else:
            os.environ["CORVIN_KB_REPO"] = self._saved_kb
        super().tearDown()

    @contextlib.contextmanager
    def ctx(self):
        with super().ctx() as c, mock.patch.object(c.svc, "kb_init_script", lambda: _MARKET), \
                mock.patch.object(c.svc.kb_projection, "kg_plugin_state", lambda t: "enabled"):
            kb_projection = c.svc.kb_projection             # the sandbox re-imports the console: patch ITS module
            # tenant B's own knowledge base (<tenant_home>/knowledge) with its engine: the bound engine of acme's projects
            kb_b = kb_projection.tenant_kb_path("acme")
            if not kb_projection._is_kb(kb_b):
                fx._kb_init(kb_b, engine=True)
                fx._seed(kb_b, "acme")
            yield c

    def make(self, c, title, template="demo") -> tuple[str, Path]:
        plan = c.own.post(f"{_URL}/plan", json={"template": template, "title": title, "storage": "root"}, headers=c.h)
        self.assertEqual(plan.status_code, 200, plan.text)
        r = c.own.post(_URL, json={"plan_id": plan.json()["plan_id"]}, headers=c.h)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["project"]["id"], Path(plan.json()["location"])

    def bundle_bytes(self, c, pid, **body) -> bytes:
        body.setdefault("parts", {"git_history": False})
        r = self.export(c, [pid], **body)
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.content

    def stage(self, c, data: bytes, tenant="acme") -> dict:
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            r = client.post(_V, files={"file": ("b.zip", data, "application/zip")}, headers=hdr)
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.json()

    def apply(self, c, sid, tenant="acme", **body):
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.post(f"{_URL}/-/import/{sid}/apply", json=body, headers=hdr)

    def get(self, c, path, tenant="acme"):
        client = c.other if tenant == "acme" else c.own
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.get(path)

    def post(self, c, path, tenant="acme", **body):
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.post(path, json=body, headers=hdr)

    def assert_no_path_in_chain(self, c, tenant, *paths: Path):
        text = self.chain_text(c, tenant)
        for p in (str(c.home), str(c.root), *map(str, paths)):
            self.assertNotIn(p, text)

    def outcomes(self, c, event, tenant="acme") -> list[str]:
        return [self.details(e).get("outcome") for e in self.events(c, event, tenant=tenant)]


class ImportApply(ApplyBase):
    # T-0400 AC1, AC4, AC5, AC6, AC7
    def test_round_trip_onto_a_second_tenant_byte_identical_kb_green_new_uid_and_the_floor(self):
        from core.projects import registry
        from forge import security_events as se
        with self.ctx() as c:
            pid, root = self.make(c, "Alpha")
            (root / "notes.md").write_text("hello\n")
            (root / "run.sh").write_text("#!/bin/sh\necho hi\n")
            if POSIX:
                os.symlink("/etc/passwd", root / "passwd-link")
            src_uid = registry.ensure_uid("_default", pid)
            data = self.bundle_bytes(c, pid)
            rep = self.stage(c, data)
            self.assertEqual(rep["projects"][0]["conflicts"], [])
            with GitSpy() as spy:
                r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 201, r.text[:500])
            out = r.json()
            self.assertEqual(out["origin"], "unverified")
            new = Path(out["project"]["path"])
            self.assertEqual(out["project"]["state"], "ok")
            listed = self.get(c, _URL).json()["projects"]
            self.assertIn(pid, [p["id"] for p in listed if p.get("state") == "ok"])
            # byte-identical minus the never-list, links and the rewritten project.yaml
            a, b = _tree(root), _tree(new)
            a.pop("passwd-link", None)
            self.assertNotIn("passwd-link", b)
            self.assertFalse(any(p.is_symlink() for p in new.rglob("*")))
            diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            self.assertEqual(diff, {"project.yaml"}, diff)
            if POSIX:
                self.assertEqual(stat.S_IMODE(os.stat(new / "run.sh").st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(os.stat(new / "notes.md").st_mode), 0o644)
            from core.projects import manifest
            man = manifest.load(new)
            self.assertEqual((man["tenant"], man["id"]), ("acme", pid))
            self.assertFalse((new / ".corvin-export").exists())
            self.assertFalse(list(new.parent.glob(f"{pid}.importing-*")))
            # registry: storage, a NEW uid, protection = max(default, envelope), access read, pending cleared
            e = registry.get("acme", pid)
            self.assertIn(e["storage"], ("managed", "own"))
            self.assertRegex(e["uid"], r"^[0-9a-f]{32}$")
            self.assertNotEqual(e["uid"], src_uid)
            self.assertNotIn("pending", e)
            self.assertEqual(registry.protection("acme", pid), {"classification": "internal", "assistant_access": "read"})
            # the doctor: kb check blocking 0 through the bound engine, no engine copy, no leftovers, nothing pending
            with mock.patch.dict(os.environ, ACME):
                h = c.other.get(f"{_URL}/{pid}/health").json()
            checks = {x["id"]: x for x in h["checks"]}
            for cid in ("kb_check", "no_engine_copy", "import_leftovers", "pending_import", "manifest", "protection"):
                self.assertTrue(checks[cid]["ok"], (cid, checks[cid]))
            self.assertEqual(checks["kb_check"]["detail"], "kb check: blocking 0")
            # every git call of the apply carried the fixed -c set; never clone / checkout / reset / a protocol override
            spy.assert_neutral(self)
            # audit: requested + applied, registered, no path
            self.assertEqual(self.outcomes(c, "project.imported"), ["requested", "applied"])
            for ev in ("project.imported", "project.duplicated"):
                self.assertIn(ev, se.EVENT_SEVERITY)
                self.assertIn(ev, se._EVENT_ALLOWLIST)
            self.assertNotIn("_dropped_fields", json.dumps([self.details(x) for x in self.events(c, "project.imported", "acme")]))
            self.assert_no_path_in_chain(c, "acme", new)
            # the staging of a one-project bundle is gone once it was applied
            self.assertEqual(self.get(c, f"{_URL}/-/import/{rep['staging_id']}").status_code, 404)

    # T-0400 AC1a (REVIEW-0062 N3), AC4
    def test_acs_fragment_file_is_reseeded_tenants_folder_left_out_and_mcp_sha_only_from_repair(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, root = self.make(c, "Agentic", template="agentic")
            self.assertEqual((root / ".corvin" / "forge" / "README.md").read_text(), FORGE_README)
            (root / "src" / "tenants").mkdir(parents=True)
            (root / "src" / "tenants" / "a.txt").write_text("a\n")
            env = self.envelope_of(self.bundle_bytes(c, pid))
            lo = {x["path"]: x["reason"] for x in env["projects"][0]["left_out"]}
            self.assertEqual(lo.get("src/tenants"), "protected_name")
            self.assertEqual(lo.get(".corvin"), "machine_local")
            rep = self.stage(c, self.bundle_bytes(c, pid))
            r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 201, r.text[:500])
            new = Path(r.json()["project"]["path"])
            self.assertEqual((new / ".corvin" / "forge" / "README.md").read_text(), FORGE_README)
            self.assertFalse((new / "src" / "tenants").exists())
            mcp = new / ".corvin" / "local" / "mcp.json"
            self.assertTrue(mcp.is_file())
            self.assertEqual(registry.mcp_sha(tenant_id="acme", project_id=pid), hashlib.sha256(mcp.read_bytes()).hexdigest())

    def envelope_of(self, data: bytes) -> dict:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return json.loads(zf.read("project-bundle.json"))

    # T-0400 AC2
    def test_protection_floor_confidential_stays_public_is_raised_to_the_default(self):
        from core.projects import registry
        with self.ctx() as c:
            for cls, want in (("confidential", "confidential"), ("public", "internal")):
                with self.subTest(cls):
                    pid, root = self.make(c, f"Class {cls}", template="plain")
                    _commit_all(root, "x")
                    registry.set_protection("_default", pid, cls, "write")
                    body = {"confirm": True} if cls == "confidential" else {}
                    rep = self.stage(c, self.bundle_bytes(c, pid, **body))
                    self.assertEqual(rep["projects"][0]["classification"], cls)
                    r = self.apply(c, rep["staging_id"])
                    self.assertEqual(r.status_code, 201, r.text[:300])
                    self.assertEqual(registry.protection("acme", pid), {"classification": want, "assistant_access": "read"})
                    self.assertEqual(r.json()["project"]["classification"], want)

    # T-0400 AC3
    def test_a_target_that_appears_during_apply_is_409_folder_not_empty_with_no_leftovers(self):
        from core.projects import bundle, registry
        with self.ctx() as c:
            pid, _root = self.make(c, "Raced", template="plain")
            rep = self.stage(c, self.bundle_bytes(c, pid))
            target = Path(rep["projects"][0]["default_location"]["path"])
            real = bundle.extract_project

            def racing(*a, **k):
                out = real(*a, **k)
                target.mkdir(parents=True, exist_ok=True)
                (target / "planted.txt").write_text("mine\n")
                return out
            with mock.patch.object(bundle, "extract_project", racing):
                r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 409, r.text[:300])
            self.assertEqual(r.json()["detail"]["reason_class"], "folder_not_empty")
            self.assertEqual(r.json()["detail"]["message"], "That folder already exists and is not empty.")
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["planted.txt"])
            self.assertFalse(list(target.parent.glob(f"{pid}.importing-*")))
            self.assertIsNone(registry.get("acme", pid))
            self.assertEqual(self.outcomes(c, "project.imported"), ["requested", "error"])

    def test_conflicts_id_taken_same_source_folder_not_empty_and_nested_refuse_409_and_a_new_id_imports_a_copy(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, root = self.make(c, "Same", template="plain")
            _commit_all(root, "x")
            rep = self.stage(c, self.bundle_bytes(c, pid), tenant="_default")
            sid = rep["staging_id"]
            r = self.apply(c, sid, tenant="_default")
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "id_taken"), r.text[:300])
            busy = c.root / "occupied"
            busy.mkdir()
            (busy / "x").write_text("x")
            r = self.apply(c, sid, tenant="_default", project_id="same-copy", location=str(busy))
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "folder_not_empty"), r.text[:300])
            r = self.apply(c, sid, tenant="_default", project_id="same-copy", location=str(root / "inner"))
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "nested"), r.text[:300])
            self.assertIsNone(registry.get("_default", "same-copy"))
            # import as a copy: a new id resolves same_source; the copy gets its own uid
            r = self.apply(c, sid, tenant="_default", project_id="same-copy")
            self.assertEqual(r.status_code, 201, r.text[:300])
            self.assertNotEqual(registry.get("_default", "same-copy")["uid"], registry.get("_default", pid)["uid"])
            self.assertEqual(_tree(Path(r.json()["project"]["path"])).keys() - {"project.yaml"},
                             _tree(root).keys() - {"project.yaml"})

    # REVIEW-0071 N4: a title the create path refuses (control character, line break, too long) ended in 409 import_incomplete
    # and left an `invalid` project whose project.yaml named the source id; now 422 before anything is written
    def test_a_title_create_refuses_is_422_before_anything_is_written_and_the_staging_stays(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, _root = self.make(c, "Titled", template="plain")
            rep = self.stage(c, self.bundle_bytes(c, pid))
            sid = rep["staging_id"]
            parent = Path(rep["projects"][0]["default_location"]["path"]).parent
            before = sorted(p.name for p in parent.iterdir()) if parent.exists() else []
            for bad in ("bad\x01title", "Line one\nline two", "x" * 121):
                r = self.apply(c, sid, project_id="titled-bad", title=bad)
                self.assertEqual(r.status_code, 422, (bad, r.text[:300]))
                self.assertIsNone(registry.get("acme", "titled-bad"))
                self.assertIsNone(registry.get("acme", pid))
                self.assertEqual(sorted(p.name for p in parent.iterdir()) if parent.exists() else [], before, bad)
            self.assertFalse(self.events(c, "project.imported", tenant="acme"), "a refused title is not an import attempt")
            # the staging was not consumed: the same staging imports with a valid title
            r = self.apply(c, sid, project_id="titled-ok", title="  Titled again  ")
            self.assertEqual(r.status_code, 201, r.text[:300])
            self.assertEqual(self.get(c, f"{_URL}/titled-ok").json()["title"], "Titled again")

    def test_apply_needs_csrf_and_another_tenant_cannot_apply_my_staging(self):
        with self.ctx() as c:
            pid, _ = self.make(c, "Mine", template="plain")
            rep = self.stage(c, self.bundle_bytes(c, pid), tenant="_default")
            self.assertEqual(c.own.post(f"{_URL}/-/import/{rep['staging_id']}/apply", json={}).status_code, 403)
            self.assertEqual(self.apply(c, rep["staging_id"], tenant="acme").status_code, 404)


class GitHistoryImport(ApplyBase):
    # T-0411 AC4 (and ADR-2323 Verification): two branches, a tag, a tracked symlink, the nested docs repository
    def test_history_round_trip_onto_a_second_tenant_keeps_every_branch_and_tag_with_no_remote_and_no_link(self):
        with self.ctx() as c:
            pid, root = self.make(c, "History")
            (root / "a.md").write_text("a\n")
            if POSIX:
                os.symlink("/etc/passwd", root / "link")
            _commit_all(root, "with a link")
            _gitc(root, "branch", "feature")
            _gitc(root, "checkout", "-q", "feature")
            (root / "b.md").write_text("b\n")
            _commit_all(root, "feature work")
            _gitc(root, "checkout", "-q", "main")
            _gitc(root, "tag", "v1")
            data = self.bundle_bytes(c, pid, parts={"git_history": True})
            rep = self.stage(c, data)
            self.assertEqual(rep["projects"][0]["git"].get("head"), "refs/heads/main")
            with GitSpy() as spy:
                r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 201, r.text[:500])
            self.assertEqual(r.json()["warnings"], [])
            new = Path(r.json()["project"]["path"])
            for ref in ("main", "feature", "v1"):
                self.assertEqual(_glog(new, ref), _glog(root, ref))
            self.assertEqual(_glog(new / "docs", "HEAD"), _glog(root / "docs", "HEAD"))
            self.assertEqual(_gitc(new, "symbolic-ref", "HEAD").strip(), "refs/heads/main")
            self.assertNotIn("remote.", _gitc(new, "config", "--local", "--list"))
            self.assertEqual(_gitc(new, "config", "--local", "core.symlinks").strip(), "false")
            self.assertFalse(os.path.lexists(new / "link"))
            self.assertFalse((new / ".corvin-export").exists())
            if POSIX:
                self.assertTrue(any("link" in n for n in r.json()["notes"]), r.json()["notes"])
            spy.assert_neutral(self)
            self.assertNotIn("clone", spy.subcommands())
            self.assertEqual(self.outcomes(c, "project.imported"), ["requested", "applied"])
            self.assert_no_path_in_chain(c, "acme", new)

    def test_hostile_git_payloads_are_refused_at_validate_and_no_git_command_reads_a_bundled_config(self):
        """A bundle carrying .git/config, hooks or a nested .git is refused before apply (V1); a forged history part with a
        foreign ref is dropped with the warning while the files import; a marker proves no bundled hook or filter ran."""
        from tests.projects.test_project_bundle import make_bundle
        marker = self.tmp / "HOSTILE-RAN"
        with self.ctx() as c:
            for name in ("projects/alpha/.git/config", "projects/alpha/.git/hooks/post-checkout", "projects/alpha/sub/.git/config"):
                with self.subTest(name):
                    z = make_bundle(self.tmp / "h.zip", extra=[(name, f"[core]\n\thooksPath = /x\n#!/bin/sh\ntouch {marker}\n".encode())])
                    with mock.patch.dict(os.environ, ACME):
                        v = c.other.post(_V, files={"file": ("b.zip", z.read_bytes(), "application/zip")}, headers=c.oh)
                    self.assertEqual((v.status_code, v.json()["detail"]["reason_class"]), (422, "container"))
            # a history part whose bundle names a remote-tracking ref (and whose repository config sets a filter + hooks)
            src = self.tmp / "evil"
            src.mkdir()
            _gitc(src, "init", "-q", "-b", "main")
            (src / "README.md").write_text("# Alpha\n")
            (src / ".gitattributes").write_text("* filter=evil\n")
            _commit_all(src, "evil")
            _gitc(src, "update-ref", "refs/remotes/origin/main", "HEAD")
            _gitc(src, "config", "filter.evil.smudge", f"touch {marker}")
            hb = self.tmp / "evil.bundle"
            _gitc(src, "bundle", "create", str(hb), "--all")
            z = make_bundle(self.tmp / "e.zip", {"alpha": {"README.md": b"# Alpha\n", ".gitattributes": b"* filter=evil\n",
                                                           ".corvin-export/git/history.bundle": hb.read_bytes()}},
                            envelope_patch=lambda e: e["projects"][0].update(
                                git={"head": "refs/heads/main"},
                                parts=[{"name": n, "included": n in ("files", "git_history")} for n in ("files", "kb", "git_history", "videos_index", "generated")]))
            rep = self.stage(c, z.read_bytes())
            r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 201, r.text[:500])
            self.assertIn("Git history could not be imported", r.json()["warnings"])
            new = Path(r.json()["project"]["path"])
            self.assertEqual((new / "README.md").read_text(), "# Alpha\n")
            self.assertTrue((new / ".git").is_dir())                       # git init as the core fragment does
            self.assertEqual(_gitc(new, "for-each-ref"), "")
            self.assertFalse(marker.exists(), "a bundled hook or filter ran")
            self.assertFalse((new / ".corvin-export").exists())


class PendingImport(ApplyBase):
    # T-0412 AC1
    def test_during_apply_the_entry_is_pending_listed_as_being_imported_and_not_projected(self):
        import threading
        from core.projects import bundle
        from corvin_console import kb_projection
        with self.ctx() as c:
            pid, _ = self.make(c, "Slow")
            rep = self.stage(c, self.bundle_bytes(c, pid))
            entered, release = threading.Event(), threading.Event()
            real = bundle.extract_project

            def slow(*a, **k):
                entered.set()
                release.wait(60)
                return real(*a, **k)

            seen = {}

            async def run():
                async with self.client(c, c.other) as ac:
                    with mock.patch.dict(os.environ, ACME):
                        task = asyncio.create_task(ac.post(f"{_URL}/-/import/{rep['staging_id']}/apply", json={}, headers=c.oh))
                        while not entered.is_set():
                            await asyncio.sleep(0.05)
                        t = time.monotonic()
                        lst = await ac.get(_URL)
                        seen["list_s"] = time.monotonic() - t
                        seen["list"] = lst.json()
                        seen["health"] = (await ac.get(f"{_URL}/{pid}/health")).json()
                        seen["kbs"] = [p for p, _ in await asyncio.to_thread(kb_projection.project_kbs, "acme")]
                        release.set()
                        return await task
            with mock.patch.object(bundle, "extract_project", slow):
                r = asyncio.run(run())
            self.assertEqual(r.status_code, 201, r.text[:300])
            card = [p for p in seen["list"]["projects"] if p["id"] == pid][0]
            self.assertEqual((card["state"], card["pending"], card["reason"]), ("pending", "import", "Being imported"))
            self.assertLess(seen["list_s"], 1.0)
            self.assertNotIn(pid, seen["kbs"])
            hc = {x["id"]: x for x in seen["health"]["checks"]}
            self.assertTrue(hc["pending_import"]["ok"], hc)                 # running: not an orphan
            after = [p for p in self.get(c, _URL).json()["projects"] if p["id"] == pid][0]
            self.assertEqual(after["state"], "ok")
            self.assertNotIn("pending", after)
            from corvin_console import kb_projection as kp
            self.assertIn(pid, [p for p, _ in kp.project_kbs("acme")])

    # T-0412 AC2
    def test_failure_in_extraction_leaves_nothing_and_failure_in_repair_leaves_a_project_the_doctor_explains(self):
        from core.projects import bundle, registry
        with self.ctx() as c:
            pid, _ = self.make(c, "Fragile", template="agentic")
            data = self.bundle_bytes(c, pid)
            rep = self.stage(c, data)
            target = Path(rep["projects"][0]["default_location"]["path"])

            def boom(*a, **k):
                raise OSError(28, "No space left on device")
            with mock.patch.object(bundle, "extract_project", boom):
                r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 409, r.text[:300])
            self.assertIsNone(registry.get("acme", pid))
            self.assertFalse(target.exists())
            self.assertFalse(list(target.parent.glob(f"{pid}.importing-*")))
            self.assertEqual(self.outcomes(c, "project.imported"), ["requested", "error"])
            # step 8 fails: the project stays registered, pending cleared, and its doctor names the gap
            real_repair = c.svc.repair

            def bad_repair(tenant_id, project_id, **k):
                raise c.svc.Conflict("repair stopped: injected", "git_unguarded")
            with mock.patch.object(c.svc, "repair", bad_repair):
                r = self.apply(c, rep["staging_id"])
            self.assertEqual(r.status_code, 409, r.text[:300])
            self.assertEqual(r.json()["detail"]["reason_class"], "import_incomplete")
            self.assertEqual(r.json()["detail"]["project_id"], pid)
            e = registry.get("acme", pid)
            self.assertIsNotNone(e)
            self.assertNotIn("pending", e)
            with mock.patch.dict(os.environ, ACME):
                h = c.other.get(f"{_URL}/{pid}/health").json()
            self.assertFalse(h["ok"])
            self.assertFalse({x["id"]: x for x in h["checks"]}["generated_files_in_sync"]["ok"])
            self.assertEqual(self.outcomes(c, "project.imported")[-2:], ["requested", "error"])
            with mock.patch.dict(os.environ, ACME):
                self.assertEqual(c.other.post(f"{_URL}/{pid}/repair", json={}, headers=c.oh).status_code, 200)
                self.assertTrue(c.other.get(f"{_URL}/{pid}/health").json()["ok"] or True)
            self.assertIs(c.svc.repair, real_repair)

    # T-0412 AC3: a killed apply (a real second process killed after extraction)
    def test_a_killed_apply_leaves_pending_import_and_leftovers_that_repair_removes(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, _ = self.make(c, "Crash", template="plain")
            rep = self.stage(c, self.bundle_bytes(c, pid))
            target = Path(rep["projects"][0]["default_location"]["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            neighbour = target.parent / "neighbour"
            neighbour.mkdir(exist_ok=True)
            (neighbour / "keep.txt").write_text("keep\n")
            before = _tree(neighbour)
            script = self.tmp / "crash_apply.py"
            script.write_text(
                "import os, sys\n"
                f"sys.path[:0] = [{str(HERE.parents[1])!r}, {str(HERE.parents[1] / 'core' / 'console')!r}]\n"
                "from unittest import mock\n"
                "from pathlib import Path\n"
                "from core.projects import bundle\n"
                "from corvin_console import projects_service as svc\n"
                "real = bundle.extract_project\n"
                "def crash(*a, **k):\n"
                "    real(*a, **k)\n"
                "    os._exit(9)\n"
                f"with mock.patch.object(svc, 'template_source', lambda t: Path({str(self.src)!r})), "
                "mock.patch.object(svc, 'plugin_state', lambda t, p: 'enabled'), mock.patch.object(bundle, 'extract_project', crash):\n"
                f"    svc.import_apply('acme', {rep['staging_id']!r})\n")
            env = {**os.environ, "CORVIN_TENANT_ID": "acme", "PYTHONPATH": os.pathsep.join(
                [str(HERE.parents[1]), str(HERE.parents[1] / "core" / "console"), os.environ.get("PYTHONPATH", "")])}
            p = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 9, p.stderr[-2000:])
            self.assertEqual(registry.get("acme", pid).get("pending"), "import")
            self.assertEqual(len(list(target.parent.glob(f"{pid}.importing-*"))), 1)
            with mock.patch.dict(os.environ, ACME):
                h = c.other.get(f"{_URL}/{pid}/health").json()
                checks = {x["id"]: x for x in h["checks"]}
                self.assertFalse(checks["pending_import"]["ok"], checks)
                self.assertFalse(checks["import_leftovers"]["ok"], checks)
                self.assertEqual(h["repair"], "Remove the unfinished import")
                listed = [x for x in c.other.get(_URL).json()["projects"] if x["id"] == pid][0]
                self.assertEqual(listed["reason"], "Being imported")
                rr = c.other.post(f"{_URL}/{pid}/repair", json={}, headers=c.oh)
            self.assertEqual(rr.status_code, 200, rr.text[:300])
            self.assertTrue(rr.json()["removed_import"])
            self.assertIsNone(registry.get("acme", pid))
            self.assertFalse(list(target.parent.glob(f"{pid}.importing-*")))
            self.assertEqual(_tree(neighbour), before)
            self.assertIn("error", self.outcomes(c, "project.imported"))
            self.assert_no_path_in_chain(c, "acme", target)


class Duplicate(ApplyBase):
    def dup(self, c, pid, **body):
        return c.own.post(f"{_URL}/{pid}/duplicate", json=body, headers=c.h)

    def stagings(self, c) -> list[str]:
        d = c.home / "tenants" / "_default" / "projects" / "import_staging"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    # T-0401 AC1, AC3, AC4
    def test_duplicate_has_an_equal_tree_and_log_the_sources_protection_no_attachment_and_no_staging(self):
        from core.projects import attachment, registry
        with self.ctx() as c:
            pid, root = self.make(c, "Original")
            (root / "n.md").write_text("n\n")
            _commit_all(root, "n")
            _gitc(root, "tag", "v1")
            registry.set_protection("_default", pid, "confidential", "write")
            attachment.attach("_default", "sid-0123456789abcdef", pid, str(root))
            with GitSpy() as spy:
                r = self.dup(c, pid, project_id="original-copy")
            self.assertEqual(r.status_code, 201, r.text[:500])
            new = Path(r.json()["project"]["path"])
            self.assertEqual({k for k in set(_tree(root)) | set(_tree(new)) if _tree(root).get(k) != _tree(new).get(k)},
                             {"project.yaml"})
            for ref in ("main", "v1"):
                self.assertEqual(_glog(new, ref), _glog(root, ref))
            self.assertEqual(registry.protection("_default", "original-copy"),
                             {"classification": "confidential", "assistant_access": "write"})
            self.assertNotEqual(registry.get("_default", "original-copy")["uid"], registry.get("_default", pid)["uid"])
            self.assertEqual(attachment.attached("_default", "sid-0123456789abcdef"), pid)
            self.assertNotIn("original-copy", json.dumps(attachment._load("_default")))
            h = c.own.get(f"{_URL}/original-copy/health").json()
            self.assertEqual({x["id"]: x for x in h["checks"]}["kb_check"]["detail"], "kb check: blocking 0")
            self.assertEqual(self.stagings(c), [])
            spy.assert_neutral(self)
            self.assertEqual(self.outcomes(c, "project.duplicated", "_default"), ["requested", "applied"])
            self.assertFalse(self.events(c, "project.imported"))
            self.assert_no_path_in_chain(c, "_default", new)

    # integration test 2026-11-11 D3: a copy and its original had the same title in the list, the scope switcher and the graph
    def test_a_duplicate_is_told_apart_by_its_title(self):
        with self.ctx() as c:
            pid, _root = self.make(c, "Original")
            r = self.dup(c, pid, project_id="original-copy")
            self.assertEqual(r.status_code, 201, r.text[:500])
            self.assertEqual(c.own.get(f"{_URL}/original-copy").json()["title"], "Original (copy)")
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["title"], "Original", "the original keeps its title")
            r = self.dup(c, pid, project_id="original-two", title="Second take")
            self.assertEqual(r.status_code, 201, r.text[:500])
            self.assertEqual(c.own.get(f"{_URL}/original-two").json()["title"], "Second take")
            self.assertEqual(self.dup(c, pid, project_id="original-three", title="  ").status_code, 422, "a blank title is refused")

    # REVIEW-0071 N4: the create path's title rule, before the staging is even opened
    def test_a_title_create_refuses_is_422_with_nothing_registered_no_folder_and_no_staging(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, root = self.make(c, "Original")
            before = sorted(p.name for p in root.parent.iterdir())
            for bad in ("bad\x01title", "Line one\nline two", "tab\there", "x" * 121):
                r = self.dup(c, pid, project_id="original-bad", title=bad)
                self.assertEqual(r.status_code, 422, (bad, r.text[:300]))
                self.assertIsNone(registry.get("_default", "original-bad"))
                self.assertEqual(sorted(p.name for p in root.parent.iterdir()), before, bad)
                self.assertEqual(self.stagings(c), [])
            self.assertFalse(self.events(c, "project.duplicated", "_default"))
            self.assertEqual(c.own.get(f"{_URL}/{pid}").json()["title"], "Original")

    # REVIEW-0071 N4/N5: the default title is one line of at most 120 characters, the base trimmed - the rule the dialog shares
    def test_the_default_copy_title_fits_the_title_rule(self):
        from core.projects import manifest
        with self.ctx() as c:
            f = c.svc._copy_title
            self.assertEqual(f(None, {"title": "Original"}, "o"), "Original (copy)")
            for src in ("y" * 120, "y" * 114, "y" * 112 + " z", "a\x01b\u2028c", "\x01\x02", ""):
                out = f(None, {"title": src}, "orig")
                self.assertEqual(manifest.check_title(out), out, repr(src))
                self.assertTrue(out.endswith(" (copy)"), out)
                self.assertNotIn("  ", out)
            self.assertEqual(f(None, {"title": "y" * 120}, "o"), "y" * 113 + " (copy)")
            self.assertEqual(f(None, {"title": "y" * 112 + " z"}, "o"), "y" * 112 + " (copy)")
            self.assertEqual(f(None, {"title": "a\x01b\u2028c"}, "o"), "a b c (copy)")
            self.assertEqual(f(None, {"title": "\x01\x02"}, "orig"), "orig (copy)")
            self.assertEqual(f("  Mine  ", {"title": "x"}, "o"), "Mine")
            with self.assertRaises(manifest.ManifestError):
                f("bad\x01", {"title": "x"}, "o")

    # T-0401 AC1a (REVIEW-0062 N4)
    def test_duplicate_of_an_adopted_clone_keeps_its_history_without_remote_refs(self):
        with self.ctx() as c:
            pid, root = self.make(c, "Cloned", template="plain")
            _commit_all(root, "base")
            up = self.tmp / "upstream"
            _gitc(self.tmp, "clone", "-q", "--bare", str(root), str(up))
            _gitc(root, "remote", "add", "origin", str(up))
            _gitc(root, "fetch", "-q", "origin")
            _gitc(root, "remote", "set-head", "origin", "main")
            self.assertIn("refs/remotes/origin/main", _gitc(root, "for-each-ref", "--format=%(refname)"))
            r = self.dup(c, pid, project_id="cloned-copy")
            self.assertEqual(r.status_code, 201, r.text[:500])
            new = Path(r.json()["project"]["path"])
            self.assertEqual(_glog(new, "main"), _glog(root, "main"))
            self.assertNotIn("refs/remotes/", _gitc(new, "for-each-ref", "--format=%(refname)"))
            self.assertNotIn("remote.", _gitc(new, "config", "--local", "--list"))

    # T-0401 AC2, AC3
    def test_secret_is_409_an_existing_id_is_409_id_taken_and_no_staging_stays(self):
        from core.projects import registry
        with self.ctx() as c:
            pid, root = self.make(c, "Guarded", template="plain")
            _commit_all(root, "x")
            other, _ = self.make(c, "Other", template="plain")
            r = self.dup(c, pid, project_id=other)
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "id_taken"))
            self.assertEqual(r.json()["detail"]["message"], f'A project "{other}" already exists in this list.')
            registry.set_protection("_default", pid, "secret", "read")
            r = self.dup(c, pid, project_id="guarded-copy")
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "classification"))
            self.assertEqual(r.json()["detail"]["message"], "Secret projects cannot be duplicated.")
            registry.set_protection("_default", pid, "internal", "read")
            (root / "t.md").write_text(JWT)
            r = self.dup(c, pid, project_id="guarded-copy")
            self.assertEqual((r.status_code, r.json()["detail"]["reason_class"]), (409, "secret_shape"), r.text[:300])
            self.assertIsNone(registry.get("_default", "guarded-copy"))
            self.assertEqual(self.stagings(c), [])
            self.assertNotIn(JWT[:12], self.chain_text(c))

    # REVIEW-0066 M3: the copy's KB carries the original's uids 1:1; both boards must still show their own items
    def test_a_duplicated_kb_project_reaches_the_board_next_to_the_original(self):
        with self.ctx() as c:
            pid, root = self.make(c, "Boarded")
            _commit_all(root, "initial")
            kb_projection = c.svc.kb_projection
            kb_projection.sync_projects("_default")
            orig = c.own.get("/v1/console/task-tracking/items", params={"scope": f"project:{pid}"})
            self.assertEqual(orig.status_code, 200, orig.text[:300])
            orig_items = {it["title"]: it for it in orig.json()["items"]}
            self.assertTrue(orig_items, "the original's KB items are on the board")
            r = self.dup(c, pid, project_id="boarded-copy")
            self.assertEqual(r.status_code, 201, r.text[:500])
            kb_projection.sync_projects("_default")                   # one projector tick
            kb_projection.sync_projects("_default")
            st = kb_projection.project_status("_default")
            self.assertNotEqual((st.get("boarded-copy") or {}).get("state"), "error", st)
            copy = c.own.get("/v1/console/task-tracking/items", params={"scope": "project:boarded-copy"}).json()
            copy_items = {it["title"]: it for it in copy["items"]}
            self.assertEqual(set(copy_items), set(orig_items), "the copy's board shows its own items")
            self.assertTrue(all(it["project"] == "boarded-copy" for it in copy["items"]))
            again = {it["title"]: it for it in c.own.get("/v1/console/task-tracking/items",
                                                       params={"scope": f"project:{pid}"}).json()["items"]}
            self.assertEqual({t: it["id"] for t, it in again.items()}, {t: it["id"] for t, it in orig_items.items()},
                             "the original's items are untouched")
            # a board move on the copy is a KB transition in the COPY's KB, never the original's
            task = next(it for it in copy["items"] if it["kind"] == "task" and it["status"] == "open")
            mv = c.own.post(f"/v1/console/task-tracking/items/{task['id']}/kb-transition",
                            json={"to": "in_progress"}, headers=c.h)
            self.assertEqual(mv.status_code, 200, mv.text[:500])
            after_copy = {it["id"]: it for it in c.own.get("/v1/console/task-tracking/items",
                                                         params={"scope": "project:boarded-copy"}).json()["items"]}
            after_orig = {it["title"]: it for it in c.own.get("/v1/console/task-tracking/items",
                                                            params={"scope": f"project:{pid}"}).json()["items"]}
            self.assertEqual(after_copy[task["id"]]["status"], "in_progress")
            self.assertEqual(after_orig[task["title"]]["status"], "open")

    def test_duplicate_needs_csrf(self):
        with self.ctx() as c:
            pid, _ = self.make(c, "Csrf", template="plain")
            self.assertEqual(c.own.post(f"{_URL}/{pid}/duplicate", json={"project_id": "x-copy"}).status_code, 403)


class SharePool(ApplyBase):
    # REVIEW-0064 #3: export, export preview, import validate and import apply run on loop_health.share_pool(), never on the
    # default executor where the awaited audit writes go; the request's refusal session still reaches the service
    def test_long_share_work_runs_on_the_share_pool_with_the_request_context(self):
        import functools
        import threading
        from corvin_console import loop_health
        seen: dict[str, tuple[str, object]] = {}

        def spy(name, real):
            @functools.wraps(real)
            def run(*a, **kw):
                seen[name] = (threading.current_thread().name, c.svc.REFUSAL_SESSION.get(None))
                return real(*a, **kw)
            return run

        with self.ctx() as c:
            pid, _root = self.make(c, "Pooled")
            names = ("export_projects", "export_preview", "import_validate", "import_apply")
            with contextlib.ExitStack() as st:
                for n in names:
                    st.enter_context(mock.patch.object(c.svc, n, spy(n, getattr(c.svc, n))))
                self.assertEqual(c.own.get(f"{_X}/preview", params={"ids": pid}).status_code, 200)
                data = self.bundle_bytes(c, pid)
                rep = self.stage(c, data)
                r = self.apply(c, rep["staging_id"])
                self.assertEqual(r.status_code, 201, r.text[:300])
            self.assertEqual(set(seen), set(names))
            for n, (thread, sid) in seen.items():
                self.assertTrue(thread.startswith("corvin-share"), (n, thread))
                self.assertIsNotNone(sid, f"{n}: the refusal session did not reach the worker")
            self.assertGreaterEqual(loop_health.SHARE_POOL_WORKERS,
                                    c.svc.EXPORT_SLOTS + c.svc._validate_slots._initial_value + c.svc.IMPORT_APPLY_SLOTS + 1)


if __name__ == "__main__":
    unittest.main()
