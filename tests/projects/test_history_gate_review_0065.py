"""REVIEW-0065 V1 - the history gate of the project export scans the objects the bundle carries, whatever the repository
says about its own history.

Three ways a repository can show git a different history than its objects hold: replace refs (``refs/replace/*``), a
graft file (``info/grafts``) and a shallow file (``shallow``). Each one used to hide a committed token from the gate, or
to make the export write a history that cannot be imported. Same sandbox as ``test_project_bundle.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_project_bundle import JWT, BundleBase, _commit_all, _gitc  # noqa: E402

from core.projects import bundle, bundle_rules, changes  # noqa: E402


class HiddenHistory(BundleBase):

    def _token_commit_then_clean(self, root: Path) -> tuple[str, str]:
        """A commit carrying a token, then one removing it. Returns (token commit, its parent)."""
        parent = _gitc(root, "rev-parse", "HEAD").strip()
        (root / "cfg.txt").write_text(f"token = {JWT}\n")
        _commit_all(root, "add cfg")
        bad = _gitc(root, "rev-parse", "HEAD").strip()
        (root / "cfg.txt").unlink()
        _commit_all(root, "drop cfg")
        return bad, parent

    def _exported_history(self, z: Path, pid: str) -> str:
        with zipfile.ZipFile(z) as zf:
            (self.tmp / "h.bundle").write_bytes(zf.read(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}"))
        clone = self.tmp / "clone"
        _gitc(self.tmp, "init", "-q", "--bare", str(clone))
        _gitc(clone, "fetch", "-q", str(self.tmp / "h.bundle"), "refs/heads/*:refs/heads/*")
        return _gitc(clone, "--no-replace-objects", "log", "-p", "--all")

    def test_a_replace_ref_does_not_hide_a_committed_token(self):
        pid, root = self.project()
        _gitc(root, "checkout", "-q", "-b", "side")
        bad, _parent = self._token_commit_then_clean(root)
        _gitc(root, "checkout", "-q", "-")
        good = _gitc(root, "rev-parse", "HEAD").strip()
        _gitc(root, "replace", "-f", bad, good)
        with self.assertRaises(bundle.BundleRefused) as cm:
            self.build([pid], git_history=True)
        self.assertEqual((cm.exception.reason_class, cm.exception.path), ("secret_in_history", "cfg.txt"))
        self.assertEqual(bundle.preview("_default", [pid], bundle.Parts())["projects"][0]["history_findings"], 1)

    def test_a_graft_does_not_hide_a_committed_token(self):
        pid, root = self.project()
        bad, parent = self._token_commit_then_clean(root)
        top = _gitc(root, "rev-parse", "HEAD").strip()
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "grafts").write_text(f"{top} {parent}\n")     # the token commit is cut out of the chain
        with self.assertRaises(bundle.BundleRefused) as cm:
            self.build([pid], git_history=True)
        self.assertEqual((cm.exception.reason_class, cm.exception.path), ("secret_in_history", "cfg.txt"))

    def test_a_graft_without_a_token_exports_the_real_history(self):
        pid, root = self.project()
        parent = _gitc(root, "rev-parse", "HEAD").strip()
        (root / "a.txt").write_text("a\n")
        _commit_all(root, "a")
        (root / "b.txt").write_text("b\n")
        _commit_all(root, "b")
        top = _gitc(root, "rev-parse", "HEAD").strip()
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "grafts").write_text(f"{top} {parent}\n")
        z, _ = self.build([pid], git_history=True)
        log = self._exported_history(z, pid)
        self.assertIn("a.txt", log)                                    # the grafted-out commit travelled: the bundle is whole

    def test_a_shallow_repository_leaves_the_history_out_with_a_warning(self):
        pid, root = self.project()
        self._token_commit_then_clean(root)
        top = _gitc(root, "rev-parse", "HEAD").strip()
        (root / ".git" / "shallow").write_text(top + "\n")             # hand-written: the parents exist but git hides them
        z, res = self.build([pid], git_history=True)
        self.assertNotIn(f"projects/{pid}/{bundle_rules.HISTORY_BUNDLE}", self.names(z))
        self.assertIn(bundle.SHALLOW_WARNING, res.warnings)
        pv = bundle.preview("_default", [pid], bundle.Parts())
        self.assertIn(bundle.SHALLOW_WARNING, pv["warnings"])
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                self.assertNotIn(JWT.encode(), zf.read(n), n)

    def test_every_git_call_ignores_replace_refs_and_grafts(self):
        env = changes.neutral_git_env(self.tmp, {"PATH": os.environ.get("PATH", "")})
        self.assertEqual(env.get("GIT_NO_REPLACE_OBJECTS"), "1")
        self.assertEqual(env.get("GIT_GRAFT_FILE"), os.devnull)
        self.assertEqual(changes._env().get("GIT_NO_REPLACE_OBJECTS"), "1")
        self.assertIn("core.useReplaceRefs=false", " ".join(changes.neutral_git_args(self.tmp)))


class ImportRefSet(BundleBase):
    """The import side: a bundle naming ``refs/replace/*`` is not accepted (only branches and tags are)."""

    def test_a_bundle_carrying_a_replace_ref_drops_the_history_part(self):
        self.assertFalse(bundle.import_ref_ok("refs/replace/" + "0" * 40))
        src = self.tmp / "src-repo"
        src.mkdir()
        _gitc(src, "init", "-q", "-b", "main")
        (src / "a.txt").write_text("a\n")
        _commit_all(src, "a")
        first = _gitc(src, "rev-parse", "HEAD").strip()
        (src / "b.txt").write_text("b\n")
        _commit_all(src, "b")
        _gitc(src, "replace", "-f", _gitc(src, "rev-parse", "HEAD").strip(), first)
        b = self.tmp / "r.bundle"
        _gitc(src, "bundle", "create", str(b), "--all")
        self.assertIn("refs/replace/", _gitc(src, "bundle", "list-heads", str(b)))
        tree = self.tmp / "tree"
        tree.mkdir()
        (tree / "a.txt").write_text("a\n")
        res = bundle.git_import(tree, b, "refs/heads/main")
        self.assertFalse(res.ok)
        self.assertFalse((tree / ".git").exists())


if __name__ == "__main__":
    unittest.main()
