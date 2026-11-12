"""T-0361 / CONCEPT-0126 §5.6 - "New video": one run creates the video's folder, its sources.md (eight sections),
a row in the project's videos/INDEX.md and, with the knowledge base on, the epic VIDEO-<name> with the DE and EN
tasks. Without a knowledge base the folder and sources.md are created and the response says so.

Real everything except the tenant seams (template source, plugin states, kb_init): the template is the REAL
catalogue of the Corvin-Marketplace plugin project_templates, the knowledge base and its engine are real, and the
fresh `kb check` of the project is the proof that the epic and tasks are well formed.
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
from _projects_fixtures import sandbox_home_env  # noqa: E402
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_MARKET = fx._projects_root() / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management"
_SRC = _MARKET / "project_templates" / "src"
_KB_INIT = _MARKET / "corvin_knowledge" / "src" / "kb_init.py"
_URL = "/v1/console/projects"
_ENABLED = {"corvin_knowledge": "enabled", "project_templates": "enabled", "video_producer": "enabled"}
SECTIONS = ["Videoeigenschaften", "Inhalt", "Reproduktion", "Konfiguration", "Pipeline",
            "Quellen im Codebase", "Bekannte Einschränkungen", "Verwandte ADRs/Konzepte"]


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file() and _SRC.is_dir() and "--no-engine" in _KB_INIT.read_text(),
                     "needs Corvin-Knowledge and the Corvin-Marketplace plugins")
class ProjectVideosE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._home_env = sandbox_home_env(self.tmp)       # the temp dir is the own home (ADR-2321 10a)
        self._home_env.__enter__()
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        self._home_env.__exit__(None, None, None)
        if self.saved is None:
            os.environ.pop("CORVIN_KB_REPO", None)
        else:
            os.environ["CORVIN_KB_REPO"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self):
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
                    yield client, {"X-CSRF-Token": csrf}, root, home
        return cm()

    def _project(self, c, hdr, title="Lie detector videos"):
        p = c.post(f"{_URL}/plan", json={"template": "video-engineering", "title": title}, headers=hdr).json()
        r = c.post(_URL, json={"plan_id": p["plan_id"]}, headers=hdr)
        self.assertEqual(r.status_code, 201, r.text)
        return p["project_id"]

    def test_one_run_makes_the_folder_the_sources_file_the_index_row_and_the_kb_epic_with_de_and_en_tasks(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            r = c.post(f"{_URL}/{pid}/videos", json={"name": "plumbline-intro"}, headers=hdr)
            self.assertEqual(r.status_code, 201, r.text)
            v = r.json()["video"]
            vdir = Path(v["path"])
            self.assertEqual(vdir, root / "Corvin-Videos" / "plumbline-intro")
            self.assertTrue((vdir / "source" / ".gitkeep").is_file() and (vdir / "build" / ".gitkeep").is_file())
            # the convention: sources.md with its eight sections, named after the video
            src = (vdir / "sources.md").read_text(encoding="utf-8")
            self.assertTrue(src.startswith("# sources.md - plumbline-intro\n"), src[:80])
            self.assertEqual([s for s in SECTIONS if f"## {s}" in src], SECTIONS)
            self.assertEqual(src.count("\n## "), 8)
            # the project's index has the row
            idx = (root / pid / "videos" / "INDEX.md").read_text(encoding="utf-8")
            self.assertIn("| plumbline-intro | yes | yes |", idx)
            # the knowledge base: the epic and the DE/EN tasks, and the KB is still well formed
            self.assertTrue(v["kb"] and v["epic"])
            titles = sorted(p.read_text(encoding="utf-8").split("title:", 1)[1].splitlines()[0].strip().strip("'\"")
                            for p in (root / pid / "docs" / "kb" / "tasks").glob("*.md"))
            for want in ("Narration DE", "Render DE", "Narration EN", "Render EN", "Check numbers DE against EN",
                         "Acceptance and sources.md"):
                self.assertIn(want, titles)
            self.assertEqual(len(v["tasks"]), 6)
            epic = (root / pid / "docs" / "kb" / "epics").glob("*.md")
            self.assertTrue(any("VIDEO-plumbline-intro" in p.read_text(encoding="utf-8") for p in epic))
            chk = json.loads(fx._sh(sys.executable, self.bound / "scripts" / "kb.py", "--repo", root / pid / "docs", "check"))
            self.assertEqual(chk["blocking"], 0, chk)
            # audit: requested, then applied, with a count and no path
            recs = [e for e in _audit_events(home) if e.get("event_type") == "project.video_created"]
            outcomes = [(e.get("details") or {}).get("outcome") for e in recs]
            self.assertEqual(outcomes, ["requested", "applied"])
            self.assertNotIn(str(self.tmp), json.dumps(recs))

    def test_without_a_knowledge_base_only_the_folder_and_sources_are_made_and_the_answer_says_so(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            off = c.put(f"{_URL}/{pid}/features", json={"set": {"kb": False}, "cascade": True}, headers=hdr)
            self.assertEqual(off.status_code, 200, off.text)
            r = c.post(f"{_URL}/{pid}/videos", json={"name": "no-kb-video"}, headers=hdr)
            self.assertEqual(r.status_code, 201, r.text)
            v = r.json()
            self.assertFalse(v["video"]["kb"])
            self.assertIsNone(v["video"]["epic"])
            self.assertEqual(v["video"]["tasks"], [])
            self.assertIn("No knowledge base", v["note"])
            vdir = root / "Corvin-Videos" / "no-kb-video"
            self.assertTrue((vdir / "sources.md").is_file() and (vdir / "source").is_dir())
            self.assertFalse((root / pid / "docs" / "kb" / "epics").exists() and
                             any("VIDEO-" in p.name for p in (root / pid / "docs" / "kb" / "epics").glob("*.md")))

    def test_names_and_existing_folders_are_refused_before_anything_is_written(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            for bad in ("Bad Name", "../escape", "a/b", "-leading", "x" * 61, ""):
                r = c.post(f"{_URL}/{pid}/videos", json={"name": bad}, headers=hdr)
                self.assertIn(r.status_code, (400, 422), f"{bad!r}: {r.text}")
            self.assertFalse((root / "Corvin-Videos").exists(), "a refused name created the videos folder")
            self.assertEqual(c.post(f"{_URL}/{pid}/videos", json={"name": "dup-one"}, headers=hdr).status_code, 201)
            again = c.post(f"{_URL}/{pid}/videos", json={"name": "dup-one"}, headers=hdr)
            self.assertEqual(again.status_code, 409, again.text)
            self.assertEqual(again.json()["detail"]["reason_class"], "exists")

    # REVIEW-0068 #1: the shipped default storage is the managed workspace; New video must work there and for an own folder
    def test_new_video_works_in_the_managed_workspace_the_shipped_default(self):
        with self._ctx() as (c, hdr, root, home):
            p = c.post(f"{_URL}/plan", json={"template": "video-engineering", "title": "Managed videos", "storage": "managed"},
                       headers=hdr)
            self.assertEqual(p.status_code, 200, p.text)
            r = c.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=hdr)
            self.assertEqual(r.status_code, 201, r.text)
            pid = p.json()["project_id"]
            proj = Path(c.get(f"{_URL}/{pid}").json()["path"])
            self.assertEqual(c.get(f"{_URL}/{pid}").json()["storage"], "managed")
            v = c.post(f"{_URL}/{pid}/videos", json={"name": "managed-intro"}, headers=hdr)
            self.assertEqual(v.status_code, 201, v.text)
            vdir = Path(v.json()["video"]["path"])
            self.assertEqual(vdir, (proj / "videos" / "managed-intro").resolve(), "a managed project keeps its videos inside")
            self.assertTrue((vdir / "sources.md").is_file())
            self.assertIn("| managed-intro | yes | yes |", (proj / "videos" / "INDEX.md").read_text(encoding="utf-8"))
            # a managed project created from the former template default (`root: ../Corvin-Videos`) works too
            import yaml
            m = yaml.safe_load((proj / "project.yaml").read_text())
            m["videos"]["root"] = "../Corvin-Videos"
            (proj / "project.yaml").write_text(yaml.safe_dump(m))
            v2 = c.post(f"{_URL}/{pid}/videos", json={"name": "legacy-root"}, headers=hdr)
            self.assertEqual(v2.status_code, 201, v2.text)
            self.assertEqual(Path(v2.json()["video"]["path"]), (proj / "videos" / "legacy-root").resolve())
            # an explicit root outside the project and outside the projects folder is still refused (the location rule holds)
            m["videos"]["root"] = "../elsewhere"
            (proj / "project.yaml").write_text(yaml.safe_dump(m))
            bad = c.post(f"{_URL}/{pid}/videos", json={"name": "outside"}, headers=hdr)
            self.assertEqual(bad.status_code, 400, bad.text)
            self.assertFalse((proj.parent / "elsewhere").exists())

    def test_new_video_works_for_an_own_folder_outside_the_projects_folder(self):
        with self._ctx() as (c, hdr, root, home):
            own = self.tmp / "work" / "own-videos"
            p = c.post(f"{_URL}/plan", json={"template": "video-engineering", "title": "Own videos", "location": str(own)},
                       headers=hdr)
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual(c.post(_URL, json={"plan_id": p.json()["plan_id"]}, headers=hdr).status_code, 201)
            pid = p.json()["project_id"]
            v = c.post(f"{_URL}/{pid}/videos", json={"name": "own-intro"}, headers=hdr)
            self.assertEqual(v.status_code, 201, v.text)
            self.assertEqual(Path(v.json()["video"]["path"]), (own / "videos" / "own-intro").resolve())
            self.assertFalse((own.parent / "Corvin-Videos").exists())

    def test_a_missing_template_is_refused_before_any_folder_or_record(self):
        # REVIEW-0090 N43: the template check ran after the record and after the videos root was made, which stayed behind
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            proj = Path(c.get(f"{_URL}/{pid}").json()["path"])
            (proj / "videos" / "TEMPLATE-sources.md").unlink()
            vroot_before = {p.name for p in root.iterdir()}
            chain = Path(home) / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            n_before = chain.read_text().count('"project.video_created"') if chain.is_file() else 0
            v = c.post(f"{_URL}/{pid}/videos", json={"name": "first"}, headers=hdr)
            self.assertEqual(v.status_code, 400, v.text)
            self.assertIn("restore it with Repair", v.text)
            self.assertEqual({p.name for p in root.iterdir()}, vroot_before, "a folder was left behind")
            n_after = chain.read_text().count('"project.video_created"') if chain.is_file() else 0
            self.assertEqual(n_after, n_before, "a refused video wrote audit records")

    def test_the_videos_feature_must_be_on(self):
        with self._ctx() as (c, hdr, root, home):
            pid = self._project(c, hdr)
            # video_producer depends on videos: the switch-off needs the cascade, and the answer is checked
            off = c.put(f"{_URL}/{pid}/features", json={"set": {"videos": False}, "cascade": True}, headers=hdr)
            self.assertEqual(off.status_code, 200, off.text)
            r = c.post(f"{_URL}/{pid}/videos", json={"name": "off-video"}, headers=hdr)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["reason_class"], "feature_off")
            self.assertFalse((root / "Corvin-Videos" / "off-video").exists())


if __name__ == "__main__":
    unittest.main()
