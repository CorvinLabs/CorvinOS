"""REVIEW-0066 m2: a crafted catalogue whose fragment writes a control file is refused when it is loaded.

The reviewer's catalogue planned ``.git/config`` (an fsmonitor command the operator's next terminal ``git status`` runs) and a
``.claude/settings.json`` with hooks. Every protected target is refused by ``load_catalogue``; the targets the real
catalogue uses (``.corvin/forge/README.md``, a nested ``.gitignore``) still load.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import _projects_fixtures as fx  # noqa: E402

from core.projects import fragments as F  # noqa: E402

PROTECTED = [".git/config", ".git/hooks/post-checkout", ".claude/settings.json", ".claude/settings.local.json",
             "sub/.claude/settings.json", ".vscode/tasks.json", ".github/workflows/ci.yml", "project.yaml", "nested/project.yaml",
             ".mcp.json", ".gitattributes", "src/.gitattributes", ".gitmodules", ".envrc", ".corvin/local/mcp.json",
             ".corvin/registry.json", "docs/kb/_meta/sources.yaml", "kb/_meta/sources.yaml", ".GIT/config", ".git./config",
             "Project.YAML"]
ALLOWED = [".corvin/forge/README.md", "docs/data/scratch/.gitignore", "README.md", ".github/ISSUE_TEMPLATE.md", "notes/kb/x.md"]


class FragmentTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _catalogue(self, dst: str) -> Path:
        src = self.tmp / dst.replace("/", "_").replace(".", "d")
        fx.write_fragment(src, "core", texts={"files/f": "x\n"},
                          **{"files": [{"src": "files/f", "dst": dst, "kind": "seed"}], "actions": ["git_init"]})
        fx.write_template(src, "demo", ["core"])
        return src

    def test_protected_targets_are_refused_at_load(self):
        for dst in PROTECTED:
            with self.subTest(dst=dst), self.assertRaises(F.FragmentError) as cm:
                F.load_catalogue(self._catalogue(dst))
            self.assertIn("protected", str(cm.exception))

    def test_ordinary_and_engine_owned_targets_still_load(self):
        for dst in ALLOWED:
            with self.subTest(dst=dst):
                cat = F.load_catalogue(self._catalogue(dst))
                self.assertEqual(cat.fragments["core"].files[0]["dst"], dst)

    def test_the_real_catalogue_loads(self):
        real = fx_market()
        if real is None:
            self.skipTest("needs the Corvin-Marketplace checkout")
        cat = F.load_catalogue(real)
        self.assertIn("acs", cat.fragments)
        self.assertEqual(F.validate_catalogue(cat), [])


def fx_market() -> Path | None:
    here = Path(__file__).resolve()
    for up in here.parents:
        cand = up / "Corvin-Marketplace" / "plugins" / "buildin" / "knowledge_management" / "project_templates" / "src"
        if cand.is_dir():
            return cand
    return None


if __name__ == "__main__":
    unittest.main()
