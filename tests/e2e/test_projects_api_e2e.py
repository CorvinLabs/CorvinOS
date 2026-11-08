"""T-0355 / ADR-2315 - the projects API, driven over real HTTP with real sessions for two tenants.

Real everything except the template SOURCE and the tenant plugin states (the template plugin
itself is T-0354): the bound knowledge base and its engine are real (``CORVIN_KB_REPO``), the
project's ``docs/`` is scaffolded by the real ``kb_init.py --no-engine`` of the Knowledge Graph
plugin from the sibling Marketplace checkout, ``kb new`` seeds it, git is real, and the proof of a
healthy project is a fresh ``kb.py --repo <docs> check`` subprocess.

What must hold: create -> ``kb check`` blocking 0; toggle round trip leaves an empty git diff and
touches no user file; adopt writes no byte; path rules refuse before anything is created; another
tenant sees 404; a missing tenant plugin refuses with nothing written; every mutation needs CSRF;
the audit records carry project ids and enums only, none is dropped by the allowlist.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "core" / "console" / "tests"))
import test_project_kb_projector_e2e as fx  # noqa: E402
from _projects_fixtures import small_catalogue  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/projects"
_MARKET = fx._projects_root() / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management" / "corvin_knowledge" / "src" / "kb_init.py"


def _tree_hash(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _git(root: Path, *a: str) -> str:
    return fx._sh("git", "-C", root, *a)


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file() and _MARKET.is_file() and "--no-engine" in _MARKET.read_text(),
                     "needs Corvin-Knowledge and a Corvin-Marketplace checkout whose kb_init has --no-engine")
class ProjectsApiE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self.src = small_catalogue(self.tmp / "src")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)
        self.plugins = {"corvin_knowledge": "enabled", "project_templates": "enabled"}

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CORVIN_KB_REPO", None)
        else:
            os.environ["CORVIN_KB_REPO"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self):
        """Sandboxed console with two tenants and the host seams (template source, plugin state, kb_init) pointed at fixtures."""
        class Ctx:
            pass
        outer = self

        import contextlib

        @contextlib.contextmanager
        def cm():
            with _sandbox(outer.tmp / "s", tenants=("_default", "acme")) as (_c, _t, home, clients):
                from core.projects import registry
                from corvin_console import projects_service as svc
                root = outer.tmp / "projects"
                root.mkdir(exist_ok=True)
                registry.set_projects_root("_default", str(root))
                registry.set_projects_root("acme", str(root))
                with mock.patch.object(svc, "template_source", lambda tenant: outer.src), \
                        mock.patch.object(svc, "plugin_state", lambda tenant, pid: outer.plugins.get(pid, "absent")), \
                        mock.patch.object(svc, "kb_init_script", lambda: _MARKET):
                    c = Ctx()
                    c.home, c.root, c.svc = home, root, svc
                    c.own, c.own_csrf = clients["_default"]
                    c.other, c.other_csrf = clients["acme"]
                    yield c
        return cm()

    def _create(self, c, title="Lie detector videos", **extra):
        plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": title, **extra},
                          headers={"X-CSRF-Token": c.own_csrf})
        self.assertEqual(plan.status_code, 200, plan.text)
        r = c.own.post(_URL, json={"plan_id": plan.json()["plan_id"]}, headers={"X-CSRF-Token": c.own_csrf})
        return plan.json(), r

    # ── create ─────────────────────────────────────────────────────────────

    def test_templates_plan_create_then_a_fresh_kb_check_is_green(self):
        with self._ctx() as c:
            t = c.own.get(f"{_URL}/templates").json()
            self.assertTrue(t["available"] and t["templates"][0]["id"] == "demo", t)
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Lie detector videos"},
                              headers={"X-CSRF-Token": c.own_csrf})
            self.assertEqual(plan.status_code, 200, plan.text)
            p = plan.json()
            self.assertEqual(p["project_id"], "lie-detector-videos")
            self.assertTrue(p["ready"] and p["plugins_needed"] == ["corvin_knowledge"], p)
            self.assertFalse((c.root / p["project_id"]).exists(), "plan wrote something")
            r = c.own.post(_URL, json={"plan_id": p["plan_id"]}, headers={"X-CSRF-Token": c.own_csrf})
            self.assertEqual(r.status_code, 201, r.text)
            proj = c.root / "lie-detector-videos"
            self.assertTrue((proj / "project.yaml").is_file() and (proj / "README.md").is_file())
            # the proof: a FRESH engine process on the project's docs/
            chk = json.loads(fx._sh(sys.executable, self.bound / "scripts" / "kb.py", "--repo", proj / "docs", "check"))
            self.assertEqual(chk["blocking"], 0, chk)
            self.assertFalse((proj / "docs" / "scripts").exists(), "an engine copy was written into the project")
            self.assertEqual(len(list((proj / "docs" / "kb" / "initiatives").glob("*.md"))), 1)
            self.assertEqual(len(list((proj / "docs" / "kb" / "tasks").glob("*.md"))), 1)
            self.assertIn("initialise", _git(proj / "docs", "log", "--oneline"))          # nested: docs is its own repo
            self.assertEqual(_git(proj, "status", "--porcelain"), "")                     # project repo clean, docs/ ignored
            self.assertNotIn("docs/", _git(proj, "ls-files"))
            # registry + reads
            lst = c.own.get(_URL).json()
            self.assertEqual([x["id"] for x in lst["projects"]], ["lie-detector-videos"])
            got = c.own.get(f"{_URL}/lie-detector-videos").json()
            self.assertEqual(got["state"], "ok")
            self.assertEqual(sorted(got["active"]), ["core", "kb", "tasks", "videos"])
            h = c.own.get(f"{_URL}/lie-detector-videos/health").json()
            self.assertTrue(h["ok"], h)
            self.assertIn("kb_check", [x["id"] for x in h["checks"]])

    def test_path_rules_refuse_before_anything_is_created(self):
        with self._ctx() as c:
            outside = self.tmp / "elsewhere" / "p"
            for loc in (str(outside), str(c.root / "a" / ".." / "b"), "relative/p", str(c.home / "tenants" / "_default" / "p"),
                        str(c.root)):
                r = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "X", "location": loc},
                               headers={"X-CSRF-Token": c.own_csrf})
                self.assertEqual(r.status_code, 422, f"{loc}: {r.text}")
                self.assertEqual(r.json()["detail"]["reason_class"], "path_rule")
                self.assertNotIn(str(self.tmp), json.dumps(r.json()), "the refusal leaks a path")
            self.assertFalse((self.tmp / "elsewhere").exists())
            self.assertEqual(list(c.root.iterdir()), [])
            recs = [e for e in _audit_events(c.home) if e.get("event_type") == "project.blocked"]
            self.assertTrue(recs and all((e.get("details") or {}).get("reason_class") == "path_rule" for e in recs))

    def test_a_missing_tenant_plugin_refuses_and_nothing_is_written(self):
        with self._ctx() as c:
            plan = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Needs kb"},
                              headers={"X-CSRF-Token": c.own_csrf}).json()
            self.plugins["corvin_knowledge"] = "installed"
            self.assertFalse(c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Needs kb two"},
                                        headers={"X-CSRF-Token": c.own_csrf}).json()["ready"])
            r = c.own.post(_URL, json={"plan_id": plan["plan_id"]}, headers={"X-CSRF-Token": c.own_csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "plugin_missing")
            self.assertEqual(list(c.root.iterdir()), [])
            self.assertEqual(c.own.get(_URL).json()["projects"], [])

    # ── tenant isolation, CSRF, audit ──────────────────────────────────────

    def test_another_tenant_sees_404_and_cannot_use_a_foreign_plan(self):
        with self._ctx() as c:
            plan, r = self._create(c)
            self.assertEqual(r.status_code, 201, r.text)
            pid = plan["project_id"]
            self.assertEqual(c.other.get(_URL).json()["projects"], [])
            for method, url in (("get", f"{_URL}/{pid}"), ("get", f"{_URL}/{pid}/health"),
                                ("delete", f"{_URL}/{pid}"), ("post", f"{_URL}/{pid}/open"),
                                ("post", f"{_URL}/{pid}/repair"), ("put", f"{_URL}/{pid}/features")):
                kw = {"headers": {"X-CSRF-Token": c.other_csrf}}
                if method == "put":
                    kw["json"] = {"set": {"tasks": False}}
                if method == "post" and url.endswith("repair"):
                    kw["json"] = {}
                resp = getattr(c.other, method)(url, **kw)
                self.assertEqual(resp.status_code, 404, f"{method} {url}: {resp.status_code} {resp.text}")
            plan2 = c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "Second"}, headers={"X-CSRF-Token": c.own_csrf}).json()
            r = c.other.post(_URL, json={"plan_id": plan2["plan_id"]}, headers={"X-CSRF-Token": c.other_csrf})
            self.assertEqual(r.status_code, 404, r.text)
            self.assertFalse((c.root / "second").exists())
            adopt = c.other.post(f"{_URL}/adopt", json={"location": str(c.root / pid)}, headers={"X-CSRF-Token": c.other_csrf})
            self.assertEqual(adopt.status_code, 422, adopt.text)         # the directory belongs to the first tenant

    def test_mutations_need_csrf_and_a_session(self):
        with self._ctx() as c:
            self.assertEqual(c.own.post(f"{_URL}/plan", json={"template": "demo", "title": "X"}).status_code, 403)
            self.assertEqual(c.own.post(_URL, json={"plan_id": "x" * 12}).status_code, 403)
            c.own.cookies.clear()
            self.assertIn(c.own.get(_URL).status_code, (401, 403))

    def test_audit_records_carry_ids_and_enums_only(self):
        with self._ctx() as c:
            plan, r = self._create(c)
            c.own.post(f"{_URL}/{plan['project_id']}/open", headers={"X-CSRF-Token": c.own_csrf})
            recs = [e for e in _audit_events(c.home) if str(e.get("event_type", "")).startswith("project.")]
            kinds = [(e["event_type"], (e.get("details") or {}).get("outcome")) for e in recs]
            self.assertIn(("project.plan_created", None), kinds)
            self.assertIn(("project.created", "requested"), kinds)
            self.assertIn(("project.created", "applied"), kinds)
            self.assertIn(("project.opened", None), kinds)
            self.assertLess(kinds.index(("project.created", "requested")), kinds.index(("project.created", "applied")))
            blob = json.dumps(recs)
            self.assertNotIn(str(self.tmp), blob, "an audit record carries a path")
            self.assertTrue(all((e.get("details") or {}).get("tenant_id") == "_default" for e in recs))
            self.assertFalse([e for e in recs if (e.get("details") or {}).get("_dropped_fields")], "the allowlist dropped a field")
            # the chain these records sit in still verifies end to end (hash links intact)
            from forge import security_events as se  # noqa: PLC0415
            from core.paths import tenant_audit_chain  # noqa: PLC0415
            ok, problems = se.verify_chain(Path(tenant_audit_chain("_default")))
            self.assertTrue(ok, problems[:3])

    # ── features, repair, open, adopt ──────────────────────────────────────

    def test_toggle_round_trip_leaves_no_diff_and_dependencies_are_enforced(self):
        with self._ctx() as c:
            plan, r = self._create(c)
            pid = plan["project_id"]
            proj = c.root / pid
            hdr = {"X-CSRF-Token": c.own_csrf}
            (proj / "README.md").write_text("# my own readme\n", encoding="utf-8")
            _git(proj, "-c", "user.name=u", "-c", "user.email=u@u", "commit", "-qam", "user: own readme")
            before = _tree_hash(proj)
            git_before = _git(proj / "docs", "rev-parse", "HEAD")
            # kb has an active dependant (tasks): refused without cascade, nothing changes
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"kb": False}}, headers=hdr)
            self.assertEqual(r.status_code, 409, r.text)
            d = r.json()["detail"]
            self.assertEqual((d["reason_class"], d["dependants"]), ("has_dependants", ["tasks"]))
            self.assertEqual(_tree_hash(proj), before)
            # a leaf switches off and on again with an empty diff
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"tasks": False}}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("tasks", r.json()["active"])
            self.assertTrue((proj / "docs" / "kb").is_dir(), "switching off removed files")
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"tasks": True}}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(_tree_hash(proj), before, "the round trip changed a file")
            self.assertEqual(_git(proj, "status", "--porcelain"), "")
            self.assertEqual(_git(proj / "docs", "rev-parse", "HEAD"), git_before)
            self.assertEqual((proj / "README.md").read_text(), "# my own readme\n")
            # switching on something that needs more: refused until the dependency is accepted
            c.own.put(f"{_URL}/{pid}/features", json={"set": {"tasks": False}}, headers=hdr)
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"acs": True}}, headers=hdr)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["requires"], ["claude"])
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"acs": True}, "accept_dependencies": True}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue((proj / ".mcp.json").is_file() and (proj / "notes" / "x.md").is_file())
            # machine-local files are ignored, the seed of the new feature is the only new tracked content
            self.assertNotIn(".mcp.json", _git(proj, "status", "--porcelain"))
            # core cannot be switched off; unknown features are refused
            self.assertEqual(c.own.put(f"{_URL}/{pid}/features", json={"set": {"core": False}}, headers=hdr).status_code, 409)
            self.assertEqual(c.own.put(f"{_URL}/{pid}/features", json={"set": {"ghost": True}}, headers=hdr).status_code, 400)
            # cascade switches the dependants off in one confirmed step; files stay
            r = c.own.put(f"{_URL}/{pid}/features", json={"set": {"claude": False}, "cascade": True}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse({"claude", "acs"} & set(r.json()["active"]))
            self.assertTrue((proj / "notes" / "x.md").is_file(), "cascade deleted a file")
            kinds = [(e["event_type"], (e.get("details") or {}).get("feature"), (e.get("details") or {}).get("to"))
                     for e in _audit_events(c.home) if e.get("event_type") == "project.feature_toggled"]
            self.assertIn(("project.feature_toggled", "tasks", "off"), kinds)
            self.assertIn(("project.feature_toggled", "acs", "on"), kinds)

    def test_repair_dry_run_shows_the_diff_and_keeps_user_text(self):
        with self._ctx() as c:
            plan, _ = self._create(c)
            pid = plan["project_id"]
            proj = c.root / pid
            hdr = {"X-CSRF-Token": c.own_csrf}
            self.assertEqual(c.own.post(f"{_URL}/{pid}/repair", json={"dry_run": True}, headers=hdr).json()["pending"], [])
            text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
            mine = "\n\nMy rule: äöü\n"
            tampered = text.replace("## Core", "## Core (tampered)").replace(
                "<!-- user:begin -->\n\n<!-- user:end -->", "<!-- user:begin -->" + mine + "<!-- user:end -->")
            (proj / "CLAUDE.md").write_text(tampered, encoding="utf-8")
            d = c.own.post(f"{_URL}/{pid}/repair", json={"dry_run": True}, headers=hdr).json()
            self.assertEqual([x["path"] for x in d["pending"]], ["CLAUDE.md"])
            self.assertIn("tampered", d["pending"][0]["diff"])
            self.assertEqual((proj / "CLAUDE.md").read_text(encoding="utf-8"), tampered, "a dry run wrote")
            c.own.post(f"{_URL}/{pid}/repair", json={}, headers=hdr)
            fixed = (proj / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("tampered", fixed)
            self.assertIn("<!-- user:begin -->" + mine + "<!-- user:end -->", fixed)
            self.assertIn("project.repaired", [e.get("event_type") for e in _audit_events(c.home)])

    def test_open_returns_the_context_and_remove_then_adopt_writes_no_byte(self):
        with self._ctx() as c:
            plan, _ = self._create(c)
            pid = plan["project_id"]
            proj = c.root / pid
            hdr = {"X-CSRF-Token": c.own_csrf}
            o = c.own.post(f"{_URL}/{pid}/open", headers=hdr).json()
            self.assertEqual(o["project"]["id"], pid)
            self.assertIn(str(proj), o["command"])
            self.assertFalse(o["chat_attachment"])
            before = _tree_hash(proj)
            r = c.own.delete(f"{_URL}/{pid}", headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["files_deleted"])
            self.assertEqual(c.own.get(f"{_URL}/{pid}").status_code, 404)
            self.assertEqual(_tree_hash(proj), before, "removing from the registry touched a file")
            r = c.own.post(f"{_URL}/adopt", json={"location": str(proj)}, headers=hdr)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["id"], pid)
            self.assertEqual(_tree_hash(proj), before, "adopt wrote a byte")
            self.assertEqual(c.own.post(f"{_URL}/adopt", json={"location": str(proj)}, headers=hdr).status_code, 409)
            kinds = {e.get("event_type") for e in _audit_events(c.home)}
            self.assertTrue({"project.registry_removed", "project.adopted", "project.opened"} <= kinds, kinds)

    def test_a_missing_directory_is_reported_not_dropped(self):
        with self._ctx() as c:
            plan, _ = self._create(c)
            shutil.rmtree(c.root / plan["project_id"])
            row = c.own.get(_URL).json()["projects"][0]
            self.assertEqual(row["state"], "missing")
            self.assertEqual(c.own.get(f"{_URL}/{plan['project_id']}/health").json()["ok"], False)


if __name__ == "__main__":
    unittest.main()
