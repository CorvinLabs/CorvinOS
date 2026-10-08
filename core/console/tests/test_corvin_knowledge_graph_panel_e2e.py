"""Knowledge Graph console panel (ADR-2206) — the real route, a real `kb.py index` run.

Driven through the real console router with a real session (``_sandbox``), against a
fixture Corvin-Knowledge checkout: real `kb new`, a real `kb.py index` subprocess, then
the real GET route. What must hold:

* the panel is actually mounted and reachable — not just imported (ADR-2206 Context: it
  was imported into registry.tsx but never placed into PANELS, so it was unreachable);
* `kb/graph/` (uid-keyed), not the frozen `graph/` (ADR-MESH-002), is what the route reads;
* a node's id is `uid or id` and an edge's `from_id`/`to_id` use the SAME key, so an edge
  drawn by the frontend always has both endpoints;
* an external or unresolved link produces no edge (no second node to draw one to);
* labels become `tags`.
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

_URL = "/v1/console/plugins/corvin-knowledge"


def _projects_root() -> Path:
    """Sibling repos live next to the MAIN checkout — also when this runs in a linked worktree."""
    here = Path(__file__).resolve().parent
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    return Path(r.stdout.strip()).parent.parent if r.returncode == 0 else Path(__file__).resolve().parents[4]


def _this_checkout() -> Path:
    """The CorvinOS checkout this test file itself lives in — a worktree while this
    change is being proved there, the main checkout once merged. Never a hardcoded
    sibling guess, or the reachability check would read the wrong tree's registry.tsx."""
    r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


_KB_SCRIPTS = Path(os.environ.get("CORVIN_KB_SCRIPTS") or _projects_root() / "Corvin-Knowledge" / "scripts")


def _sh(*args, cwd=None):
    r = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _kb(repo: Path, *args) -> dict:
    return json.loads(_sh(sys.executable, repo / "scripts/kb.py", "--repo", repo, *args))


@unittest.skipUnless((_KB_SCRIPTS / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KnowledgeGraphPanelE2E(unittest.TestCase):
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_graph_route_serves_the_real_kb_translated_and_filtered(self):
        kb = self.kb
        base = _kb(kb, "new", "decision", "--title", "Base decision")
        dep = _kb(kb, "new", "decision", "--title", "Depends on base")
        dep_file = next(p for p in (kb / "decisions").glob(f"{dep['id']}-*.md"))  # root: . -> top-level decisions/
        dep_file.write_text(dep_file.read_text().replace("depends_on: []", f"depends_on: [{base['id']}]"))
        ini = _kb(kb, "new", "initiative", "--title", "I")
        ep = _kb(kb, "new", "epic", "--title", "E", "--initiative", ini["id"])
        t = _kb(kb, "new", "task", "--title", "Implements it", "--epic", ep["id"],
               "--link", f"implements={dep['id']}", "--link", "source=https://example.org/x")
        tf = next(p for p in (kb / "kb/tasks").glob(f"{t['id']}-*.md"))
        tf.write_text(tf.read_text().replace("title:", "labels: [urgent, kb]\ntitle:", 1))
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")

        with _sandbox(self.tmp) as (client, csrf, _home, _):
            # The route persists to ~/.claude/plugins/corvin-knowledge.json (CONFIG_FILE is bound
            # at import). Without this redirect the test overwrote the OPERATOR's live config with
            # a temp path that is deleted afterwards (found by the 2026-10-03 adversarial review).
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            live = Path.home() / ".claude" / "plugins" / "corvin-knowledge.json"
            before = live.read_bytes() if live.exists() else None
            cfg = self.tmp / "home" / ".claude" / "plugins" / "corvin-knowledge.json"
            with mock.patch.object(kg, "CONFIG_FILE", cfg):   # restored on exit — never leaks
                r = client.post(f"{_URL}/config", json={"repo_path": str(kb)}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(cfg.is_file())
                r = client.get(f"{_URL}/graph")
            self.assertEqual(live.read_bytes() if live.exists() else None, before)   # live file untouched
            self.assertEqual(r.status_code, 200, r.text)
            g = r.json()

        by_title = {e["title"]: e for e in g["entities"]}
        self.assertEqual(by_title["Base decision"]["type"], "decision")
        self.assertEqual(by_title["Base decision"]["status"], "proposed")
        self.assertEqual(by_title["Implements it"]["tags"], ["urgent", "kb"])   # labels -> tags
        # node ids are uid-keyed (not the human id), and match what the edges use
        self.assertTrue(all(len(e["id"]) == 26 for e in g["entities"]))

        rels = {(r["relation"], by_title_id(g, r["from_id"]), by_title_id(g, r["to_id"])) for r in g["relations"]}
        self.assertIn(("depends_on", "Depends on base", "Base decision"), rels)
        self.assertIn(("implements", "Implements it", "Depends on base"), rels)
        self.assertIn(("child_of", "Implements it", "E"), rels)
        self.assertIn(("child_of", "E", "I"), rels)
        # the external `source` link has no second node -> no edge for it
        self.assertFalse(any(r["relation"] == "source" for r in g["relations"]))
        # every edge endpoint resolves to a real node (ADR-2206: uid-or-id consistently)
        ids = {e["id"] for e in g["entities"]}
        self.assertTrue(all(r["from_id"] in ids and r["to_id"] in ids for r in g["relations"]))

    def test_panel_is_a_manifest_plugin_panel_not_a_static_one(self):
        """The panel belongs to the Marketplace plugin `corvin_knowledge`: its route and its
        sidebar entry come from the capability manifest while the plugin is installed and enabled
        (same pattern as video-producer). A static PANELS/NAV_GROUPS entry would outlive the
        plugin. This pins the SOURCE wiring; that a browser really shows it under Marketplace is
        proved by tests/e2e/knowledge-graph-explorer.spec.ts against the live console."""
        web = _this_checkout() / "core" / "console" / "corvin_console" / "web-next"
        reg = (web / "src" / "panels" / "registry.tsx").read_text()
        self.assertNotIn('rc("corvin-knowledge"', reg)
        # mountable by name from the manifest: the component name the plugin declares
        self.assertRegex(reg, r"COMPONENTS_BY_NAME[^=]*=\s*\{[^}]*\bCorvinKnowledgePage,")
        nav = (web / "src" / "components" / "layout.tsx").read_text()
        self.assertNotIn("/app/corvin-knowledge", nav)
        self.assertIn('id: "marketplace"', nav)   # the group the manifest entry is appended to
        yaml_ = (_projects_root() / "Corvin-Marketplace" / "plugins" / "contributor" / "knowledge_management"
                 / "corvin_knowledge" / "plugin.yaml")
        if yaml_.is_file():
            text = yaml_.read_text()
            self.assertIn("group: marketplace", text)
            self.assertIn("component: CorvinKnowledgePage", text)


def by_title_id(graph: dict, node_id: str) -> str:
    return next(e["title"] for e in graph["entities"] if e["id"] == node_id)


if __name__ == "__main__":
    unittest.main()
