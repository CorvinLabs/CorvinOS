"""PLAN-0972 phase 5 / ADR-2321 Decisions 6, 12, 13, 14 - the trash for managed projects over real HTTP (T-0404).

The console router with real sessions for two tenants, a real filesystem, real git and the real audit chain of the sandbox
(``CORVIN_HOME`` in the test's temp dir, HOME and XDG_DATA_HOME there too, so the managed workspace is the sandbox's). The
sweep's clock is injected (``now=``), nothing sleeps.

Layout of one trash entry: ``<managed_root>/.trash/<id>-<YYYYMMDDTHHMMSSZ>/`` holds ``trash.json`` (the sidecar) and
``project/`` (the renamed project folder), so a project file called ``trash.json`` can never be overwritten and one
``rmtree`` removes the whole entry.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
sys.path.insert(0, str(HERE.parents[1]))
from _projects_fixtures import sandbox_home_env, small_catalogue, write_template  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/projects"
_T = f"{_URL}/-/trash"
POSIX = os.name != "nt"
DAY = 86400.0
ACME = {"CORVIN_TENANT_ID": "acme"}


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "link:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "dir"
    return out


class TrashBase(unittest.TestCase):
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
            from corvin_console import projects_service as svc
            with mock.patch.object(svc, "template_source", lambda tenant: outer.src), \
                    mock.patch.object(svc, "plugin_state", lambda tenant, pid: "enabled"):
                class C:
                    pass
                c = C()
                c.home, c.svc = home, svc
                c.own, c.csrf = clients["_default"]
                c.other, c.other_csrf = clients["acme"]
                c.h = {"X-CSRF-Token": c.csrf}
                c.oh = {"X-CSRF-Token": c.other_csrf}
                assert c.own.get(_URL).status_code == 200
                yield c

    # ── helpers ──
    def create(self, c, title, *, storage="managed", tenant="_default", template="plain") -> tuple[str, Path]:
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            body = {"template": template, "title": title}
            if storage == "managed":
                body["storage"] = "managed"
            plan = client.post(f"{_URL}/plan", json=body, headers=hdr)
            self.assertEqual(plan.status_code, 200, plan.text)
            r = client.post(_URL, json={"plan_id": plan.json()["plan_id"]}, headers=hdr)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["project"]["id"], Path(plan.json()["location"])

    def adopt_own(self, c, name) -> tuple[str, Path]:
        d = self.tmp / "code" / name
        d.mkdir(parents=True)
        (d / "mine.txt").write_text("my own file\n")
        r = c.own.post(f"{_URL}/adopt", json={"location": str(d)}, headers=c.h)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["storage"], "own")
        return r.json()["id"], d

    def delete(self, c, pid, confirm=None, tenant="_default"):
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.request("DELETE", f"{_URL}/{pid}/files", json={"confirm_id": pid if confirm is None else confirm}, headers=hdr)

    def trash(self, c, tenant="_default") -> list[dict]:
        client = c.other if tenant == "acme" else c.own
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            r = client.get(_T)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["entries"]

    def restore(self, c, entry, tenant="_default"):
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.post(f"{_T}/{entry}/restore", headers=hdr)

    def purge(self, c, entry, confirm, tenant="_default"):
        client, hdr = (c.other, c.oh) if tenant == "acme" else (c.own, c.h)
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return client.request("DELETE", f"{_T}/{entry}", json={"confirm_id": confirm}, headers=hdr)

    def ids(self, c, tenant="_default") -> list[str]:
        client = c.other if tenant == "acme" else c.own
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return [p["id"] for p in client.get(_URL).json()["projects"]]

    def managed_root(self, c, tenant="_default") -> Path:
        client = c.other if tenant == "acme" else c.own
        with mock.patch.dict(os.environ, ACME if tenant == "acme" else {}):
            return Path(client.get(_URL).json()["managed_root"])

    def events(self, c, name, tenant="_default"):
        return [e for e in _audit_events(c.home, tenant) if (e.get("event_type") or e.get("event")) == name]

    @staticmethod
    def details(e: dict) -> dict:
        return e.get("details") or e

    def outcomes(self, c, name, tenant="_default") -> list[str]:
        return [self.details(e).get("outcome") for e in self.events(c, name, tenant)]

    def chain_text(self, c, tenant="_default") -> str:
        f = c.home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def chat(self, c) -> str:
        r = c.own.post("/v1/console/chat/sessions", json={"title": "trash e2e"}, headers=c.h)
        self.assertIn(r.status_code, (200, 201), r.text)
        b = r.json()
        return (b.get("session") or {}).get("sid") or b.get("sid") or b.get("session_id")


class DeleteAndRestore(TrashBase):
    # AC1
    def test_delete_moves_to_trash_and_restore_brings_it_back_identical(self):
        with self.ctx() as c:
            pid, root = self.create(c, "Alpha")
            mr = self.managed_root(c)
            self.assertEqual(root.parent, mr)
            (root / "notes.md").write_text("hello\n")
            (root / "trash.json").write_text('{"mine": true}\n')            # a project file with the sidecar's name stays the user's
            sid = self.chat(c)
            self.assertEqual(c.own.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=c.h).status_code, 200)
            before = _tree(root)
            r = self.delete(c, pid)
            self.assertEqual(r.status_code, 200, r.text)
            out = r.json()
            entry = out["trash_entry"]
            self.assertRegex(entry, rf"^{pid}-\d{{8}}T\d{{6}}Z$")
            self.assertIn("restorable_until", out)
            self.assertFalse(root.exists())
            tdir = mr / ".trash" / entry
            self.assertEqual(_tree(tdir / "project"), before)
            side = json.loads((tdir / "trash.json").read_text())
            self.assertEqual((side["id"], side["title"], side["original_path"]), (pid, "Alpha", str(root)))
            if POSIX:
                self.assertEqual(oct((tdir / "trash.json").stat().st_mode & 0o777), "0o600")
                self.assertEqual(oct((mr / ".trash").stat().st_mode & 0o777), "0o700")
            # the list no longer shows it, the trash does; the project cannot be opened, attached or exported meanwhile
            lst = c.own.get(_URL).json()
            self.assertNotIn(pid, [p["id"] for p in lst["projects"]])
            self.assertEqual(lst["trash_count"], 1)
            self.assertEqual([(t["id"], t["state"]) for t in lst["trashed"]], [(pid, "trashed")])
            rows = self.trash(c)
            self.assertEqual([(t["entry"], t["id"], t["title"]) for t in rows], [(entry, pid, "Alpha")])
            self.assertEqual(c.own.get(f"{_URL}/{pid}").status_code, 409)
            self.assertEqual(c.own.get(f"{_URL}/chat-attachment", params={"session_id": sid}).json()["project_id"], None)
            self.assertEqual(c.own.post(f"{_URL}/{pid}/attach", json={"session_id": sid}, headers=c.h).status_code, 409)
            # the other tenant sees nothing of it
            self.assertEqual(self.trash(c, "acme"), [])
            self.assertEqual(self.restore(c, entry, "acme").status_code, 404)
            # restore
            r = self.restore(c, entry)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["state"], "ok")
            self.assertEqual(_tree(root), before, "the restored tree is byte-identical")
            self.assertFalse(tdir.exists())
            self.assertIn(pid, self.ids(c))
            self.assertEqual(self.trash(c), [])
            got = c.own.get(_URL).json()
            self.assertEqual((got["trash_count"], got["trashed"]), (0, []))
            self.assertEqual([p for p in got["projects"] if p["id"] == pid][0]["storage"], "managed")
            self.assertEqual(c.own.get(f"{_URL}/chat-attachment", params={"session_id": sid}).json()["project_id"], None,
                             "the attachment stays dropped")
            h = c.own.get(f"{_URL}/{pid}/health").json()
            self.assertEqual([x["id"] for x in h["checks"] if not x["ok"] and not x.get("note")], [])
            # audit: requested + applied for both, no path, chain verifies
            self.assertEqual(self.outcomes(c, "project.deleted"), ["requested", "applied"])
            self.assertEqual(self.outcomes(c, "project.restored"), ["requested", "applied"])
            text = self.chain_text(c)
            self.assertNotIn(str(self.tmp), text)
            self.assertNotIn(entry, text)
            from forge import security_events as se
            ok, problems = se.verify_chain(c.home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl")
            self.assertTrue(ok, problems)
            for ev in ("project.deleted", "project.restored"):
                self.assertEqual(se.EVENT_SEVERITY[ev], "WARNING")
                self.assertEqual(se._EVENT_ALLOWLIST[ev], frozenset({"project_id", "outcome", "tenant_id"}))
            self.assertNotIn("_dropped_fields", json.dumps([self.details(e) for e in self.events(c, "project.deleted")]))

    # AC2
    def test_an_own_project_is_never_trashed_and_a_wrong_id_is_refused(self):
        with self.ctx() as c:
            pid, d = self.adopt_own(c, "beta")
            before = _tree(d)
            r = self.delete(c, pid)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "not_managed")
            self.assertEqual(r.json()["detail"]["message"],
                             "Only projects in the Corvin workspace can be deleted here; this one lives in your own folder.")
            self.assertEqual(_tree(d), before)
            self.assertIn(pid, self.ids(c))
            mpid, mroot = self.create(c, "Gamma")
            r = self.delete(c, mpid, confirm="gamm")
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"], {"message": "Type the project id exactly to confirm.", "reason_class": "confirm_mismatch"})
            self.assertTrue((mroot / "project.yaml").is_file())
            self.assertEqual(self.delete(c, "corvin").json()["detail"]["reason_class"], "builtin")
            self.assertEqual(self.delete(c, "no-such").status_code, 404)
            r = c.own.request("DELETE", f"{_URL}/{mpid}/files", json={"confirm_id": mpid})
            self.assertEqual(r.status_code, 403, "CSRF is required")
            self.assertEqual(self.delete(c, mpid, tenant="acme").status_code, 404, "another tenant's project is unknown")
            self.assertTrue((mroot / "project.yaml").is_file())
            reasons = [self.details(e).get("reason_class") for e in self.events(c, "project.blocked")]
            self.assertIn("not_managed", reasons)
            self.assertIn("confirm_mismatch", reasons)
            self.assertEqual(self.outcomes(c, "project.deleted"), [])

    # AC3
    def test_a_folder_at_the_old_place_blocks_the_restore(self):
        with self.ctx() as c:
            pid, root = self.create(c, "Delta")
            entry = self.delete(c, pid).json()["trash_entry"]
            root.mkdir()
            (root / "planted.txt").write_text("someone else's\n")
            mr = self.managed_root(c)
            before = (_tree(mr / ".trash"), _tree(root))
            r = self.restore(c, entry)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "folder_not_empty")
            self.assertEqual((_tree(mr / ".trash"), _tree(root)), before, "nothing changed")
            self.assertEqual([t["entry"] for t in self.trash(c)], [entry])
            self.assertEqual(self.outcomes(c, "project.restored"), [])

    # AC5b
    def test_a_project_named_trash_round_trips(self):
        with self.ctx() as c:
            pid, root = self.create(c, "Trash")
            self.assertEqual(pid, "trash")
            mr = self.managed_root(c)
            self.assertEqual(root, mr / "trash")
            (root / "x.md").write_text("x\n")
            before = _tree(root)
            entry = self.delete(c, pid).json()["trash_entry"]
            self.assertTrue((mr / ".trash" / entry / "project" / "x.md").is_file())
            self.assertFalse(root.exists())
            self.assertEqual(self.restore(c, entry).status_code, 200)
            self.assertEqual(_tree(root), before)

    def test_two_tenants_keep_their_own_trash(self):
        with self.ctx() as c:
            a, _ = self.create(c, "Shared name")
            b, broot = self.create(c, "Shared name", tenant="acme")
            self.assertEqual(self.delete(c, a).status_code, 200)
            self.assertEqual([t["id"] for t in self.trash(c)], [a])
            self.assertEqual(self.trash(c, "acme"), [])
            self.assertIn(b, self.ids(c, "acme"))
            self.assertTrue((broot / "project.yaml").is_file())


class Purge(TrashBase):
    # permanent delete
    def test_permanent_delete_needs_the_typed_id_and_removes_only_that_entry(self):
        with self.ctx() as c:
            a, _ = self.create(c, "Epsilon")
            b, _ = self.create(c, "Zeta")
            keep, kroot = self.create(c, "Kept")
            ea = self.delete(c, a).json()["trash_entry"]
            eb = self.delete(c, b).json()["trash_entry"]
            mr = self.managed_root(c)
            kept_before = _tree(kroot)
            r = self.purge(c, ea, "wrong")
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.json()["detail"]["reason_class"], "confirm_mismatch")
            self.assertTrue((mr / ".trash" / ea).is_dir())
            r = self.purge(c, ea, a)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"entry": ea, "purged": True})
            self.assertFalse((mr / ".trash" / ea).exists())
            self.assertTrue((mr / ".trash" / eb / "project" / "project.yaml").is_file())
            self.assertEqual(_tree(kroot), kept_before)
            self.assertEqual([t["entry"] for t in self.trash(c)], [eb])
            self.assertNotIn(a, self.ids(c))
            from core.projects import registry
            self.assertIsNone(registry.get("_default", a), "the id is free again")
            self.assertEqual(self.outcomes(c, "project.deleted").count("purged"), 1)
            self.assertEqual(self.purge(c, ea, a).status_code, 404)

    # AC5
    def test_an_entry_outside_the_trash_is_refused(self):
        with self.ctx() as c:
            pid, root = self.create(c, "Eta")
            entry = self.delete(c, pid).json()["trash_entry"]
            mr = self.managed_root(c)
            outside = self.tmp / "outside"
            outside.mkdir()
            (outside / "marker.txt").write_text("keep me\n")
            for bad in ("..", "...", ".trash", "eta", "eta-2026", "%2e%2e", "eta-20261110T000000Z-x"):
                r = self.purge(c, bad, "eta")
                self.assertIn(r.status_code, (404, 422), (bad, r.text))
                if r.status_code == 422:
                    self.assertEqual(r.json()["detail"]["reason_class"], "path_rule", bad)
            if POSIX:
                link = mr / ".trash" / "eta-20261111T000000Z"
                os.symlink(outside, link)
                r = self.purge(c, link.name, "eta")
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["detail"]["reason_class"], "path_rule")
                self.assertEqual(self.restore(c, link.name).status_code, 422)
                link.unlink()
            self.assertEqual((outside / "marker.txt").read_text(), "keep me\n")
            self.assertEqual([t["entry"] for t in self.trash(c)], [entry])

    # AC5a
    @unittest.skipUnless(POSIX, "the junction half runs on the Windows VM")
    def test_purge_and_sweep_never_follow_a_planted_link(self):
        with self.ctx() as c:
            outside = self.tmp / "outside"
            (outside / "deep").mkdir(parents=True)
            (outside / "marker.txt").write_text("marker\n")
            (outside / "deep" / "inner.txt").write_text("inner\n")
            before = _tree(outside)
            for title in ("Link one", "Link two"):
                pid, root = self.create(c, title)
                os.symlink(outside, root / "escape")
                os.symlink(outside / "marker.txt", root / "marker-link")
                entry = self.delete(c, pid).json()["trash_entry"]
                if title == "Link one":
                    self.assertEqual(self.purge(c, entry, pid).status_code, 200)
                else:
                    res = c.svc.sweep_trash("_default", now=c.svc._now() + 31 * DAY)
                    self.assertEqual(res["purged"], 1)
                self.assertEqual(_tree(outside), before, f"{title}: the link target changed")
            self.assertEqual(self.trash(c), [])


class Sweep(TrashBase):
    # AC4
    def test_the_sweep_removes_only_entries_older_than_30_days_and_skips_on_a_saturated_host(self):
        with self.ctx() as c:
            pid, _ = self.create(c, "Theta")
            entry = self.delete(c, pid).json()["trash_entry"]
            other, oroot = self.create(c, "Theta", tenant="acme")
            with mock.patch.dict(os.environ, ACME):
                oentry = self.delete(c, other, tenant="acme").json()["trash_entry"]
            mr = self.managed_root(c)
            now = c.svc._now()
            self.assertEqual(c.svc.sweep_trash("_default", now=now + 29 * DAY)["purged"], 0)
            self.assertTrue((mr / ".trash" / entry).is_dir())
            self.assertEqual(c.svc.sweep_trash("_default", busy=True, now=now + 31 * DAY), {"purged": 0, "skipped": "busy"})
            self.assertTrue((mr / ".trash" / entry).is_dir())
            # a pending entry (a delete that has not finished) is never swept
            pend = mr / ".trash" / "iota-20260101T000000Z"
            (pend / "project").mkdir(parents=True)
            (pend / "trash.json").write_text(json.dumps({"id": "iota", "title": "Iota", "deleted_at": "2026-01-01T00:00:00Z",
                                                         "deleted_ts": 0, "state": "pending"}))
            res = c.svc.sweep_trash("_default", now=now + 31 * DAY)
            self.assertEqual(res["purged"], 1)
            self.assertFalse((mr / ".trash" / entry).exists())
            self.assertTrue(pend.is_dir(), "a pending entry is skipped")
            self.assertTrue((self.managed_root(c, "acme") / ".trash" / oentry).is_dir(), "the other tenant's trash is its own")
            self.assertEqual(self.outcomes(c, "project.deleted"), ["requested", "applied", "purged"])
            from core.projects import registry
            self.assertIsNone(registry.get("_default", pid))

    def test_the_sweep_is_bounded_per_tick(self):
        with self.ctx() as c:
            entries = []
            for n in range(12):
                pid, _ = self.create(c, f"Bulk {n}")
                entries.append(self.delete(c, pid).json()["trash_entry"])
            now = c.svc._now() + 31 * DAY
            self.assertEqual(c.svc.sweep_trash("_default", now=now)["purged"], c.svc.TRASH_SWEEP_LIMIT)
            self.assertEqual(len(self.trash(c)), 12 - c.svc.TRASH_SWEEP_LIMIT)
            self.assertEqual(c.svc.sweep_trash("_default", now=now)["purged"], 12 - c.svc.TRASH_SWEEP_LIMIT)
            self.assertEqual(self.trash(c), [])

    def test_the_projector_tick_calls_the_sweep(self):
        import inspect
        with self.ctx() as c:
            src = inspect.getsource(c.svc.kb_projection._loop)
            self.assertIn("sweep_trash(tenant_id, busy=busy)", src)


class Erasure(TrashBase):
    # ADR-2321 Decision 13: L-projects deletes nothing, trash included; the 30-day sweep bounds it
    def test_an_art17_run_leaves_the_trash_alone_and_reports_skipped(self):
        for p in (HERE.parents[1] / "corvin_operator" / "bridges" / "shared",):
            if str(p) not in sys.path:
                sys.path.append(str(p))
        with self.ctx() as c:
            pid, _ = self.create(c, "Kappa")
            entry = self.delete(c, pid).json()["trash_entry"]
            mr = self.managed_root(c)
            before = _tree(mr / ".trash")
            import erasure_handlers as eh
            from erasure_orchestrator import LayerStatus
            r = eh.ProjectsMetadataHandler("_default").purge("web:subject-1", "er-trash")
            self.assertEqual((r.status, r.count), (LayerStatus.SKIPPED, 0))
            self.assertEqual(_tree(mr / ".trash"), before)
            self.assertEqual([t["entry"] for t in self.trash(c)], [entry])


if __name__ == "__main__":
    unittest.main()
