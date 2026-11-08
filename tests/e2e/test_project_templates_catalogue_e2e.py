"""T-0354 / T-0356 / ADR-2315 - the REAL template catalogue (the Corvin-Marketplace plugin
``project_templates``), driven through the projects API.

Only the seams to the tenant (which plugin is enabled) are stubbed; the catalogue is the plugin's own
``src/`` from the sibling Marketplace checkout, the knowledge base scaffold is the real
``kb_init --no-engine``, seeding goes through the bound KB's real engine, git is real.

What must hold: the catalogue is valid and requires-closed; every template creates a project whose
fresh ``kb check`` is blocking 0; the seeded work matches the template; committed files carry no
absolute path; the machine-local MCP block appears with ``acs`` and only there; nothing a feature
switches off is deleted; the user's own block in CLAUDE.md survives.
"""
from __future__ import annotations

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
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

from core.projects import fragments as F  # noqa: E402

_MARKET = fx._projects_root() / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management"
_SRC = _MARKET / "project_templates" / "src"
_KB_INIT = _MARKET / "corvin_knowledge" / "src" / "kb_init.py"
_URL = "/v1/console/projects"
_ENABLED = {"corvin_knowledge": "enabled", "project_templates": "enabled", "video_producer": "enabled"}


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file() and _SRC.is_dir() and "--no-engine" in _KB_INIT.read_text(),
                     "needs Corvin-Knowledge and the Corvin-Marketplace plugins (project_templates, corvin_knowledge with --no-engine)")
class TemplateCatalogueE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        os.environ.pop("CORVIN_KB_REPO", None) if self.saved is None else os.environ.__setitem__("CORVIN_KB_REPO", self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sandboxed(self):
        import contextlib

        @contextlib.contextmanager
        def cm():
            with _sandbox(self.tmp / "s") as (client, csrf, home, _):
                from core.projects import registry
                from corvin_console import projects_service as svc
                root = self.tmp / "projects"
                root.mkdir(exist_ok=True)
                registry.set_projects_root("_default", str(root))
                with mock.patch.object(svc, "template_source", lambda t: _SRC), \
                        mock.patch.object(svc, "plugin_state", lambda t, p: _ENABLED.get(p, "absent")), \
                        mock.patch.object(svc, "kb_init_script", lambda: _KB_INIT):
                    yield client, {"X-CSRF-Token": csrf}, root
        return cm()

    def _create(self, client, hdr, template: str, title: str, **extra):
        p = client.post(f"{_URL}/plan", json={"template": template, "title": title, **extra}, headers=hdr)
        self.assertEqual(p.status_code, 200, p.text)
        self.assertTrue(p.json()["ready"], p.json())
        r = client.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=hdr)
        self.assertEqual(r.status_code, 201, r.text)
        return p.json()

    # ── the catalogue itself ───────────────────────────────────────────────

    def test_the_catalogue_is_valid_closed_and_free_of_absolute_paths(self):
        cat = F.load_catalogue(_SRC)
        self.assertEqual(F.validate_catalogue(cat), [])
        self.assertEqual(sorted(cat.templates), ["knowledge-only", "research-compute", "video-engineering"])
        self.assertEqual(set(cat.fragments), {"core", "kb", "tasks", "graph", "gates", "claude", "ldd", "experiments",
                                              "acs", "videos", "video_producer", "directory", "dashboard"})
        for t in cat.templates.values():                       # every default AND offered set is requires-closed
            self.assertEqual(set(F.closure(cat, t.features)), set(t.features), t.id)
            self.assertEqual(set(F.closure(cat, t.offered)), set(t.offered), t.id)
        for f in cat.fragments.values():                       # no machine path may live in a template file
            for p in f.root.rglob("*"):
                if p.is_file() and p.suffix in (".md", ".yaml", ".gitignore"):
                    text = p.read_text(encoding="utf-8")
                    for bad in ("/home/", "C:\\Users", "/Users/", "shumway"):
                        self.assertNotIn(bad, text, f"{p} contains {bad}")
        self.assertEqual(sorted(cat.fragments["acs"].mcp), ["corvin_orchestration", "forge", "skill_forge"])

    # ── real creation ──────────────────────────────────────────────────────

    def test_video_engineering_creates_a_ready_project(self):
        with self._sandboxed() as (c, hdr, root):
            tpl = [t for t in c.get(f"{_URL}/templates").json()["templates"] if t["id"] == "video-engineering"][0]
            self.assertIn("video_producer", tpl["features"])
            self.assertNotIn("acs", tpl["features"])
            self.assertIn("acs", tpl["offered"])
            p = self._create(c, hdr, "video-engineering", "Plumbline explainer")
            proj = root / p["project_id"]
            chk = json.loads(fx._sh(sys.executable, self.bound / "scripts" / "kb.py", "--repo", proj / "docs", "check"))
            self.assertEqual(chk["blocking"], 0, chk)
            for rel in ("README.md", "CLAUDE.md", "videos/INDEX.md", "videos/TEMPLATE-sources.md", "project.yaml"):
                self.assertTrue((proj / rel).is_file(), rel)
            self.assertFalse((proj / ".mcp.json").exists(), "acs is not on in this template")
            self.assertFalse((proj / "docs" / "scripts").exists())
            tasks = sorted(p_.read_text(encoding="utf-8").split("title:", 1)[1].splitlines()[0].strip().strip("'\"")
                           for p_ in (proj / "docs" / "kb" / "tasks").glob("*.md"))
            self.assertEqual(tasks, ["Capture the project goal and audience as an idea", "Check the Video Producer connection",
                                     "Create the first video", "Run the first acceptance"])
            man = (proj / "project.yaml").read_text(encoding="utf-8")
            self.assertIn("root: ../Corvin-Videos", man)
            self.assertIn("peers: []", man)
            tracked = fx._sh("git", "-C", proj, "ls-files").split()
            self.assertEqual(fx._sh("git", "-C", proj, "status", "--porcelain"), "")
            for rel in tracked:
                text = (proj / rel).read_text(encoding="utf-8", errors="replace")
                for bad in (str(self.tmp), str(Path.home()), "/home/"):
                    self.assertNotIn(bad, text, f"{rel} carries a machine path")
            claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")
            for needle in ("## Working rules", "## Knowledge base (docs/)", "## Gates", "## LDD", "## Videos", "## Video Producer", "## Peer directory"):
                self.assertIn(needle, claude)
            self.assertNotIn("Agentic compute", claude)
            self.assertIn(".corvin/local/kb-commands.md", claude)
            self.assertIn(str(self.bound / "scripts" / "kb.py"), (proj / ".corvin/local/kb-commands.md").read_text())

    def test_the_other_templates_create_and_check_green(self):
        with self._sandboxed() as (c, hdr, root), mock.patch(
                "corvin_console.projects_service.compute_worker_reachable", lambda tenant: True):
            for tpl, title in (("knowledge-only", "Notes"), ("research-compute", "Probe study")):
                p = self._create(c, hdr, tpl, title)
                proj = root / p["project_id"]
                chk = json.loads(fx._sh(sys.executable, self.bound / "scripts" / "kb.py", "--repo", proj / "docs", "check"))
                self.assertEqual(chk["blocking"], 0, (tpl, chk))
                self.assertEqual(c.get(f"{_URL}/{p['project_id']}/health").json()["ok"], True, tpl)
            ko = root / "notes"
            self.assertFalse((ko / "CLAUDE.md").exists(), "claude is not part of knowledge-only")
            self.assertFalse((ko / "videos").exists())
            rc = root / "probe-study"
            self.assertTrue((rc / ".mcp.json").is_file())
            mcp = json.loads((rc / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(mcp["mcpServers"]), ["corvin_orchestration", "forge", "skill_forge"])
            env = mcp["mcpServers"]["corvin_orchestration"]["env"]
            self.assertEqual(env["CORVIN_TENANT_ID"], "_default")
            self.assertIn(os.pathsep, env["PYTHONPATH"])
            self.assertEqual(oct((rc / ".mcp.json").stat().st_mode & 0o777), "0o600")
            tracked = fx._sh("git", "-C", rc, "ls-files").split()
            self.assertNotIn(".mcp.json", tracked)
            self.assertTrue((rc / "docs/data/experiments/README.md").is_file())
            self.assertIn("Agentic compute", (rc / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_acs_warns_about_the_worker_and_the_licence_it_cannot_verify(self):
        with self._sandboxed() as (c, hdr, root):
            from corvin_console import projects_service as svc
            with mock.patch.object(svc, "compute_worker_reachable", lambda tenant: False):
                p = c.post(f"{_URL}/plan", json={"template": "research-compute", "title": "Warn probe"}, headers=hdr).json()
                self.assertTrue(any("not reachable" in w for w in p["warnings"]), p["warnings"])
                self.assertTrue(any("licence tier" in w for w in p["warnings"]), p["warnings"])
                self.assertTrue(p["ready"], "a warning must not refuse the creation")
            with mock.patch.object(svc, "compute_worker_reachable", lambda tenant: True):
                p = c.post(f"{_URL}/plan", json={"template": "research-compute", "title": "Warn probe two"}, headers=hdr).json()
                self.assertFalse(any("not reachable" in w for w in p["warnings"]), p["warnings"])
                self.assertTrue(any("licence tier" in w for w in p["warnings"]))
                q = c.post(f"{_URL}/plan", json={"template": "video-engineering", "title": "No acs here"}, headers=hdr).json()
                self.assertFalse([w for w in q["warnings"] if "agentic compute" in w], "acs is off in this template")
                r = c.post(_URL, json={"plan_id": p["plan_id"]}, headers=hdr)
                self.assertEqual(r.status_code, 201, r.text)
                pid = p["project_id"]
                self.assertTrue(next(x for x in c.get(f"{_URL}/{pid}/health").json()["checks"] if x["id"] == "compute_worker")["ok"])
            with mock.patch.object(svc, "compute_worker_reachable", lambda tenant: False):
                h = c.get(f"{_URL}/{pid}/health").json()
                self.assertFalse(next(x for x in h["checks"] if x["id"] == "compute_worker")["ok"])
                self.assertFalse(h["ok"])

    def test_switching_acs_on_and_off_keeps_every_file_and_the_users_own_block(self):
        with self._sandboxed() as (c, hdr, root):
            p = self._create(c, hdr, "video-engineering", "Toggle probe")
            pid, proj = p["project_id"], root / p["project_id"]
            cl = proj / "CLAUDE.md"
            mine = "\n\nMine: keep this, äöü.\n"
            cl.write_text(cl.read_text(encoding="utf-8").replace("<!-- user:begin -->\n\n<!-- user:end -->",
                                                                 "<!-- user:begin -->" + mine + "<!-- user:end -->"), encoding="utf-8")
            r = c.put(f"{_URL}/{pid}/features", json={"set": {"acs": True}}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue((proj / ".mcp.json").is_file())
            self.assertIn("## Agentic compute", cl.read_text(encoding="utf-8"))
            r = c.put(f"{_URL}/{pid}/features", json={"set": {"acs": False}}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue((proj / ".mcp.json").is_file(), "switching off deleted a file")
            self.assertTrue((proj / ".corvin/forge/README.md").is_file())
            self.assertNotIn("## Agentic compute", cl.read_text(encoding="utf-8"))     # the generated section is gone after repair
            self.assertIn("<!-- user:begin -->" + mine + "<!-- user:end -->", cl.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
