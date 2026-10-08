"""Knowledge Graph Explorer, P1 (ADR-2237): GET /plugins/corvin-knowledge/doc/{key}.

Driven through the real console router with a real session against a fixture
Corvin-Knowledge checkout (real `kb new`, real `kb.py index`). The security-critical line is
containment: `path` in entities.jsonl is data, and must never become a read outside the repo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402
from test_corvin_knowledge_graph_panel_e2e import _KB_SCRIPTS, _kb, _sh  # noqa: E402

_URL = "/v1/console/plugins/corvin-knowledge"


@unittest.skipUnless((_KB_SCRIPTS / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KnowledgeDocRouteE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        kb = self.kb = self.tmp / "kb-repo"
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            (kb / "scripts" / f).write_text((_KB_SCRIPTS / f).read_text())
        (kb / "kb/_meta").mkdir(parents=True)
        (kb / "kb/_meta/sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    writable: true\n    dirs:\n"
            "      decisions: decision\n      kb/tasks: task\n      kb/epics: epic\n      kb/initiatives: initiative\n")
        (kb / "kb/audit.jsonl").write_text("")
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "add", "-A", cwd=kb)   # kb refuses to write while kb/audit.jsonl is untracked
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=kb)
        self.base = _kb(kb, "new", "decision", "--title", "Base decision")
        self.dep = _kb(kb, "new", "decision", "--title", "Depends on base")
        self.dep_file = next((kb / "decisions").glob(f"{self.dep['id']}-*.md"))
        self.dep_file.write_text(self.dep_file.read_text().replace("depends_on: []", f"depends_on: [{self.base['id']}]")
                                 + f"\nSee {self.base['id']} for the reason.\n")
        self.index()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def index(self):
        _sh(sys.executable, self.kb / "scripts/kb.py", "--repo", self.kb, "index")

    def rewrite_path(self, ident: str, new_path: str):
        p = self.kb / "kb/graph/entities.jsonl"
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        for r in rows:
            if r.get("id") == ident:
                r["path"] = new_path
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def get(self, key: str, *, login=True):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        with _sandbox(Path(tempfile.mkdtemp(dir=self.tmp))) as (client, csrf, _home, _):
            cfg = self.tmp / "home" / ".claude" / "plugins" / "corvin-knowledge.json"
            with mock.patch.object(kg, "CONFIG_FILE", cfg):
                r = client.post(f"{_URL}/config", json={"repo_path": str(self.kb)}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 200, r.text)
                if not login:
                    client.cookies.clear()
                return client.get(f"{_URL}/doc/{key}")

    def test_document_by_id_has_body_without_frontmatter_and_neighbours(self):
        r = self.get(self.dep["id"])
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["entity"]["title"], "Depends on base")
        self.assertEqual(d["entity"]["label"], self.dep["id"])
        self.assertIn(f"See {self.base['id']} for the reason.", d["markdown"])
        self.assertNotIn("depends_on:", d["markdown"])
        self.assertEqual(d["frontmatter"]["title"], "Depends on base")
        self.assertEqual([(x["relation"], x["label"]) for x in d["outgoing"]], [("depends_on", self.base["id"])])

    def test_incoming_backlink_and_uid_key(self):
        uid = self.base["uid"]
        r = self.get(uid)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["entity"]["id"], uid)
        self.assertEqual([(x["relation"], x["label"]) for x in d["incoming"]], [("depends_on", self.dep["id"])])
        self.assertEqual(d["outgoing"], [])

    def test_unknown_key_is_404(self):
        self.assertEqual(self.get("ADR-9999").status_code, 404)

    def test_requires_a_session(self):
        self.assertIn(self.get(self.dep["id"], login=False).status_code, (401, 403))

    def test_path_outside_the_repo_is_never_read(self):
        outside = self.tmp / "outside.md"
        outside.write_text("TOP SECRET")
        for bad in (str(outside), "../outside.md", "/etc/passwd"):
            with self.subTest(path=bad):
                self.rewrite_path(self.dep["id"], bad)
                r = self.get(self.dep["id"])
                self.assertEqual(r.status_code, 404, r.text)
                self.assertNotIn("TOP SECRET", r.text)

    def test_symlink_pointing_out_of_the_repo_is_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("TOP SECRET")
        link = self.kb / "decisions" / "link.md"
        os.symlink(outside, link)
        self.rewrite_path(self.dep["id"], str(link))
        r = self.get(self.dep["id"])
        self.assertEqual(r.status_code, 404, r.text)
        self.assertNotIn("TOP SECRET", r.text)

    def test_only_markdown_files_are_served(self):
        self.rewrite_path(self.dep["id"], str(self.kb / "kb" / "audit.jsonl"))
        self.assertEqual(self.get(self.dep["id"]).status_code, 404)

    def test_oversized_document_is_413(self):
        with self.dep_file.open("a") as fh:
            fh.write("x" * (600 * 1024))
        self.assertEqual(self.get(self.dep["id"]).status_code, 413)


if __name__ == "__main__":
    unittest.main()
