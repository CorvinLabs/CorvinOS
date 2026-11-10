"""T-0352 / ADR-2315 Decisions 2-5 - the fragment engine: plan writes nothing, apply is idempotent,
repair never touches user text, a broken catalogue fails the closure test.

Real filesystem and real git (the action runner of this test runs ``git init`` / ``git commit``);
the knowledge-base actions are recorded, not run (their real run is ``test_projects_api_e2e.py``).
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _projects_fixtures import ctx_for, small_catalogue, write_fragment, write_template  # noqa: E402

from core.projects import fragments as F  # noqa: E402
from core.projects.fsutil import UnsafePath  # noqa: E402


def git(root: Path, *a) -> str:
    r = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {a}: {r.stderr}")
    return r.stdout


class Runner:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, op, ctx):
        self.calls.append(op.action)
        root = Path(ctx["_root"])
        if op.action == "git_init":
            git(root, "init", "-q", "-b", "main")
        elif op.action == "git_commit":
            where = root / op.args["dir"] if op.args.get("dir") else root
            if op.args.get("dir") and not (where / ".git").exists():
                return                      # the scaffold step (a recorded no-op here) would have created it
            git(where, "add", "-A")
            git(where, "commit", "-q", "-m", op.args["message"])


class FragmentEngineE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cat = F.load_catalogue(small_catalogue(self.tmp / "src"))
        self.root = self.tmp / "proj"
        self.ctx = {**ctx_for("demo-proj", self.root), "_root": str(self.root)}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, feats=("core", "kb", "tasks", "videos"), **kw):
        return F.build_plan(self.cat, self.root, feats, self.ctx, **kw)

    def apply(self, plan, runner=None):
        return F.apply_plan(plan, self.root, self.ctx, runner or Runner())

    # ── catalogue and graph ────────────────────────────────────────────────

    def test_good_catalogue_has_no_problems_and_closure_orders_dependencies_first(self):
        self.assertEqual(F.validate_catalogue(self.cat), [])
        order = F.closure(self.cat, ["tasks"])
        self.assertEqual(order, ["core", "kb", "tasks"])
        self.assertEqual(F.closure(self.cat, ["acs"]), ["core", "claude", "acs"])
        self.assertEqual(self.plan(["tasks"]).pulled, ["core", "kb"])

    def test_broken_catalogues_fail_the_closure_test(self):
        src = self.tmp / "bad"
        write_fragment(src, "a", requires=["b"])
        write_fragment(src, "b", requires=["a"])
        self.assertTrue(any("cycle" in p for p in F.validate_catalogue(F.load_catalogue(src))))
        src2 = self.tmp / "bad2"
        write_fragment(src2, "core")
        write_fragment(src2, "x", requires=["nope"])
        self.assertTrue(any("unknown 'nope'" in p for p in F.validate_catalogue(F.load_catalogue(src2))))
        src3 = self.tmp / "bad3"
        write_fragment(src3, "core")
        write_fragment(src3, "kb", requires=["core"])
        write_fragment(src3, "tasks", requires=["kb"])
        write_template(src3, "t", ["core", "tasks"])                 # kb missing: not requires-closed
        probs = F.validate_catalogue(F.load_catalogue(src3))
        self.assertTrue(any("not requires-closed" in p and "kb" in p for p in probs), probs)
        with self.assertRaises(F.FragmentError):
            F.closure(self.cat, ["ghost"])

    def test_unknown_actions_and_unsafe_paths_are_refused_at_load(self):
        src = self.tmp / "bad4"
        write_fragment(src, "core", actions=["rm_rf"])
        with self.assertRaises(F.FragmentError):
            F.load_catalogue(src)
        src2 = self.tmp / "bad5"
        write_fragment(src2, "core", texts={"f.md": "x"}, **{"files": [{"src": "f.md", "dst": "../escape.md", "kind": "seed"}]})
        with self.assertRaises(F.FragmentError):
            F.load_catalogue(src2)

    def test_dependants_and_suggestions(self):
        act = ["core", "kb", "tasks", "gates"]
        self.assertEqual(F.dependants(self.cat, act, "kb"), ["gates", "tasks"])
        self.assertEqual(F.dependants(self.cat, act, "tasks"), [])
        self.assertEqual(F.suggestions(self.cat, ["core", "videos"]), [{"feature": "videos", "suggests": "kb"}])

    # ── plan / apply ───────────────────────────────────────────────────────

    def test_plan_writes_nothing(self):
        before = sorted(self.tmp.rglob("*"))
        plan = self.plan()
        self.assertTrue(plan.writes and not self.root.exists())
        self.assertEqual(sorted(self.tmp.rglob("*")), before)
        s = plan.summary()
        self.assertIn("README.md", [f["path"] for f in s["files"]])
        self.assertEqual(s["plugins_needed"], ["corvin_knowledge"])

    def test_apply_creates_files_and_a_second_apply_is_a_no_op_with_an_empty_git_diff(self):
        r = Runner()
        self.apply(self.plan(), r)
        self.assertEqual(r.calls[0], "git_init")
        self.assertIn("kb_scaffold", [o.action for o in self.plan(mode="repair").ops if o.kind == "action"])
        self.assertTrue((self.root / "README.md").is_file() and (self.root / "videos" / "INDEX.md").is_file())
        self.assertIn("Videos of demo-proj", (self.root / "videos" / "INDEX.md").read_text())
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        again = self.plan(mode="repair")
        self.assertEqual([o.path or o.action for o in again.pending() if o.kind == "write"], [], again.summary())
        r2 = Runner()
        self.apply(again, r2)
        self.assertNotIn("git_init", r2.calls)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(git(self.root, "diff"), "")
        self.assertEqual(git(self.root, "log", "--oneline").count("\n"), 1)

    def test_committed_files_carry_no_machine_path_and_local_files_are_not_tracked(self):
        self.apply(self.plan(("core", "kb", "claude", "acs")), Runner())
        tracked = git(self.root, "ls-files").split()
        self.assertNotIn(".mcp.json", tracked)
        self.assertFalse([t for t in tracked if t.startswith(".corvin/local")], tracked)
        self.assertTrue((self.root / ".mcp.json").is_file() and (self.root / ".corvin/local/mcp.json").is_file())
        self.assertEqual(stat.S_IMODE((self.root / ".mcp.json").stat().st_mode), 0o600)
        for rel in tracked:
            text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            for needle in ("/usr/bin/python3", "/opt/corvin_home", "/opt/corvinos", "/opt/knowledge"):
                self.assertNotIn(needle, text, f"{rel} carries {needle}")
        self.assertIn("/usr/bin/python3", (self.root / ".mcp.json").read_text())
        self.assertIn("/opt/knowledge/scripts/kb.py", (self.root / ".corvin/local/kb-commands.md").read_text())

    def test_nested_mode_keeps_docs_out_of_the_project_repository_plain_mode_does_not(self):
        self.apply(self.plan(docs_mode="nested"), Runner())
        self.assertIn("docs/", (self.root / ".gitignore").read_text())
        import shutil
        shutil.rmtree(self.root)
        self.apply(self.plan(docs_mode="plain"), Runner())
        self.assertNotIn("\ndocs/\n", (self.root / ".gitignore").read_text())

    def test_claude_md_belongs_to_the_claude_feature_and_the_scaffold_runs_before_any_file(self):
        off = self.plan(("core", "kb"))
        self.assertNotIn("CLAUDE.md", [o.path for o in off.writes])
        on = self.plan(("core", "kb", "claude"))
        self.assertIn("CLAUDE.md", [o.path for o in on.writes])
        names = [(o.kind, o.action or o.path) for o in on.ops]
        first_write = next(i for i, (k, _) in enumerate(names) if k == "write")
        self.assertLess(names.index(("action", "kb_scaffold")), first_write,
                        "kb_scaffold must run before any file lands in docs/ (it refuses a non-empty target)")
        self.assertLess(names.index(("action", "git_init")), names.index(("action", "kb_scaffold")))

    def test_nested_docs_get_their_own_commit_before_the_projects(self):
        nested = [o for o in self.plan(docs_mode="nested").ops if o.action == "git_commit"]
        self.assertEqual([o.args.get("dir") for o in nested], ["docs", None])
        plain = [o for o in self.plan(docs_mode="plain").ops if o.action == "git_commit"]
        self.assertEqual([o.args.get("dir") for o in plain], [None])

    # ── repair keeps what is the user's ────────────────────────────────────

    def test_repair_keeps_user_block_and_seed_files_and_rewrites_generated_ones(self):
        self.apply(self.plan(("core", "kb", "claude")), Runner())
        claude = self.root / "CLAUDE.md"
        text = claude.read_text(encoding="utf-8")
        mine = "\n\nMy own rule: always say hello.\nUmlauts: äöü\n"
        claude.write_text(text.replace(F.USER_BEGIN + "\n\n" + F.USER_END, F.USER_BEGIN + mine + F.USER_END)
                          .replace("## Core", "## Core (tampered)"), encoding="utf-8")
        (self.root / "README.md").write_text("# edited by hand\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.log\n" + (self.root / ".gitignore").read_text(), encoding="utf-8")
        plan = self.plan(("core", "kb", "claude"), mode="repair")
        st = {o.path: o.status for o in plan.writes}
        self.assertEqual(st["CLAUDE.md"], "changed")
        self.assertIn("tampered", next(o.diff for o in plan.writes if o.path == "CLAUDE.md"))
        self.assertEqual(st["README.md"], "skip")
        self.apply(plan, Runner())
        fixed = claude.read_text(encoding="utf-8")
        self.assertNotIn("tampered", fixed)
        self.assertIn(F.USER_BEGIN + mine + F.USER_END, fixed)              # byte-identical
        self.assertEqual((self.root / "README.md").read_text(), "# edited by hand\n")
        self.assertTrue((self.root / ".gitignore").read_text().startswith("*.log\n"))

    def test_an_existing_claude_md_that_was_not_generated_is_never_overwritten(self):
        self.root.mkdir()
        (self.root / "CLAUDE.md").write_text("# my own file\n", encoding="utf-8")
        plan = self.plan(("core", "kb", "claude"), mode="repair")
        op = next(o for o in plan.writes if o.path == "CLAUDE.md")
        self.assertEqual(op.status, "skip")
        self.assertTrue(any("CLAUDE.md" in w for w in plan.warnings))
        self.apply(plan, Runner())
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "# my own file\n")

    # ── safety ─────────────────────────────────────────────────────────────

    def test_symlinked_target_directories_are_never_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        self.root.mkdir()
        (self.root / "notes").symlink_to(outside, target_is_directory=True)     # claude fragment writes notes/x.md
        plan = self.plan(("core", "claude"), mode="repair")
        # the invariant is that nothing is written through the link; how the refusal is reported is not fixed here (review: 6250cd0b6
        # changed it from an exception to a refused item, and the link itself was never followed)
        try:
            self.apply(plan, Runner())
        except UnsafePath:
            pass
        self.assertEqual(list(outside.iterdir()), [], "a write followed the symlink")

    def test_conflicts_refuse_before_any_write(self):
        self.root.mkdir()
        (self.root / "mine.txt").write_text("x")
        plan = self.plan()
        self.assertTrue(plan.conflicts)
        with self.assertRaises(F.FragmentError):
            self.apply(plan)
        self.assertEqual([p.name for p in self.root.iterdir()], ["mine.txt"])

    def test_unknown_or_missing_placeholders_fail_closed(self):
        with self.assertRaises(F.FragmentError):
            F.substitute("{{paths.secret}}", self.ctx, "t")
        with self.assertRaises(F.FragmentError):
            F.substitute("{{paths.python}}", {"project.id": "x"}, "t")
        self.assertEqual(F.substitute("{{ project.id }}!", self.ctx, "t"), "demo-proj!")


if __name__ == "__main__":
    unittest.main()
