"""Bash-change diff (ADR-2241 amendment): unit tests of the snapshot primitive and an
E2E through ``chat_runtime.stream_turn`` against a REAL subprocess standing in for the
claude CLI. The stand-in does what the CLI does around a Bash tool: reads the
``--settings`` file, runs its PreToolUse hook command with the hook payload on stdin
(the REAL hook, ``bash_diff.py``), executes the REAL shell command, then emits the
stream-json ``user`` tool_result. Nothing about the diff is faked.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests", _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

from corvin_console import bash_diff as bd  # noqa: E402


def _drain(agen):
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


class SnapshotPrimitive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.snap = self.d / "snap"
        self.snap.mkdir()
        self.f = self.d / "f.py"
        self.f.write_text("a = 1\nb = 2\nc = 3\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, cmd, mutate, tid="toolu_1"):
        n = bd.take_snapshot(cmd, str(self.d), tid, str(self.snap))
        mutate()
        return n, bd.diff_from_snapshot(str(self.snap), tid)

    def test_sed_in_place_shows_the_hunk(self):
        n, lines = self._run("sed -i 's/b = 2/b = 20/' f.py",
                             lambda: self.f.write_text("a = 1\nb = 20\nc = 3\n"))
        self.assertEqual(n, 1)
        self.assertEqual(lines, ["@@ f.py -1,3 +1,3 @@", " a = 1", "-b = 2", "+b = 20", " c = 3"])

    def test_shell_punctuation_is_not_part_of_the_path(self):
        self.assertEqual(bd.candidate_paths("echo x > g.txt; true && (cat f.py)|wc", str(self.d)),
                         [self.d / "g.txt", self.f])

    def test_python_heredoc_naming_the_file_in_a_string(self):
        cmd = "python3 - <<'EOF'\np='f.py'\ns=open(p).read()\nopen(p,'w').write(s)\nEOF"
        _, lines = self._run(cmd, lambda: self.f.write_text("a = 1\nb = 2\nc = 4\n"))
        self.assertIn("-c = 3", lines)
        self.assertIn("+c = 4", lines)

    def test_created_file_is_all_added(self):
        _, lines = self._run("cat > new.txt <<EOF\nhi\nEOF", lambda: (self.d / "new.txt").write_text("hi\nthere\n"))
        self.assertEqual(lines, ["@@ new.txt -0,0 +1,2 @@", "+hi", "+there"])

    def test_deleted_file_is_all_removed(self):
        _, lines = self._run("rm f.py", self.f.unlink)
        self.assertEqual(lines[0], "@@ f.py -1,3 +0,0 @@")
        self.assertEqual(lines[1:], ["-a = 1", "-b = 2", "-c = 3"])

    def test_unchanged_file_has_no_diff(self):
        self.assertEqual(self._run("cat f.py", lambda: None)[1], None)

    def test_every_snapshot_is_consumed_exactly_once(self):
        self._run("sed -i x f.py", lambda: self.f.write_text("z\n"))
        self.assertEqual(list(self.snap.iterdir()), [])
        self.assertIsNone(bd.diff_from_snapshot(str(self.snap), "toolu_1"))

    def test_credential_named_and_vcs_paths_are_never_snapshotted(self):
        for name in (".env", "prod.pem", "id_rsa", "my_secret.txt"):
            (self.d / name).write_text("x\n")
        (self.d / ".git").mkdir()
        (self.d / ".git" / "config").write_text("x\n")
        cmd = "cat .env prod.pem id_rsa my_secret.txt .git/config"
        self.assertEqual(bd.take_snapshot(cmd, str(self.d), "toolu_2", str(self.snap)), 0)
        self.assertEqual(list(self.snap.iterdir()), [])

    def test_binary_and_oversized_files_are_opaque_not_creations(self):
        b = self.d / "blob.bin"
        b.write_bytes(b"\x00\x01\x02")
        big = self.d / "big.txt"
        big.write_text("x" * (bd.MAX_FILE_BYTES + 1))
        bd.take_snapshot("cat blob.bin big.txt", str(self.d), "toolu_3", str(self.snap))
        b.write_bytes(b"\x00\x09")
        big.write_text("y\n")  # shrinks below the cap: still must NOT read as "created"
        self.assertIsNone(bd.diff_from_snapshot(str(self.snap), "toolu_3"))

    def test_bad_ids_and_missing_dir_are_inert(self):
        for tid in ("", "../x", "a/b", "x" * 200):
            self.assertEqual(bd.take_snapshot("cat f.py", str(self.d), tid, str(self.snap)), 0)
            self.assertIsNone(bd.diff_from_snapshot(str(self.snap), tid))
        self.assertEqual(bd.take_snapshot("cat f.py", str(self.d), "toolu_4", str(self.d / "nope")), 0)

    def test_file_cap(self):
        for i in range(30):
            (self.d / f"f{i}.txt").write_text("x\n")
        cmd = "cat " + " ".join(f"f{i}.txt" for i in range(30))
        self.assertEqual(bd.take_snapshot(cmd, str(self.d), "toolu_5", str(self.snap)), bd.MAX_FILES)

    def test_hook_entry_never_fails_or_blocks(self):
        import subprocess
        for payload in ("not json", "{}", json.dumps({"tool_name": "Bash", "tool_input": None})):
            r = subprocess.run([sys.executable, bd.__file__], input=payload, text=True,
                               capture_output=True, env={**os.environ, bd.SNAP_ENV: str(self.snap)})
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.snap.iterdir()), [])

    def test_concurrent_snapshots_use_separate_files(self):
        import threading
        errs = []
        def work(i):
            try:
                bd.take_snapshot("cat f.py", str(self.d), f"toolu_c{i}", str(self.snap))
            except Exception as e:  # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(list(self.snap.iterdir())), 16)
        self.assertTrue(all(json.loads(p.read_text())["files"] for p in self.snap.iterdir()))

    def test_budget_diffing_a_max_size_file_is_fast(self):
        import time
        big = self.d / "big.py"
        big.write_text("\n".join(f"line {i} = {i}" for i in range(bd.MAX_FILE_LINES - 5)) + "\n")
        bd.take_snapshot("sed -i x big.py", str(self.d), "toolu_6", str(self.snap))
        big.write_text(big.read_text().replace("line 10 =", "LINE 10 =").replace("line 3000 =", "LINE 3000 ="))
        t = time.perf_counter()
        lines = bd.diff_from_snapshot(str(self.snap), "toolu_6")
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertTrue(lines and any(x.startswith("+LINE 10") for x in lines))

    def test_sweep_removes_only_old_prefixed_dirs(self):
        old = Path(bd.make_snap_dir())
        os.utime(old, (1, 1))
        fresh = Path(bd.make_snap_dir())
        try:
            bd.sweep_stale()
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
        finally:
            bd.remove_snap_dir(str(fresh))


FAKE_CLI = textwrap.dedent('''\
    #!{python}
    """Stand-in for `claude -p` that behaves like the CLI around a Bash tool."""
    import json, os, subprocess, sys
    argv = sys.argv[1:]
    settings = argv[argv.index("--settings") + 1] if "--settings" in argv else None
    sys.stdin.readline()                                    # the framed first user message
    script = json.load(open(os.environ["FAKE_BASH_SCRIPT"]))
    def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    emit({{"type": "system", "subtype": "init", "model": "claude-haiku-4-5"}})
    hooks = []
    if settings:
        for g in json.load(open(settings))["hooks"].get("PreToolUse", []):
            if g["matcher"] == "Bash":
                hooks += [h["command"] for h in g["hooks"]]
    for i, cmd in enumerate(script):
        tid = "toolu_fake%d" % i
        emit({{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "id": tid, "name": "Bash", "input": {{"command": cmd}}}}]}}}})
        payload = json.dumps({{"tool_name": "Bash", "tool_use_id": tid, "cwd": os.getcwd(),
                               "tool_input": {{"command": cmd}}}})
        for h in hooks:                                      # PreToolUse: BEFORE the tool runs
            subprocess.run(h, shell=True, input=payload, text=True, env=os.environ)
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        emit({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": tid,
              "content": r.stdout, "is_error": r.returncode != 0}}]}},
              "tool_use_result": {{"stdout": r.stdout, "stderr": r.stderr}}}})
    emit({{"type": "result", "subtype": "success", "result": "done", "is_error": False,
          "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
''')


@unittest.skipIf(sys.platform.startswith("win"), "POSIX shell in the stand-in CLI")
class BashDiffE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        cls._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"), "XDG_CONFIG_HOME": str(home / "xdg"),
            "FORGE_ROOT": str(home / "forge"), "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default", "CORVIN_OS_ENGINE": "claude_code",
        })
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (home / "corvin").mkdir()
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        cls.cr = chat_runtime
        import house_rules as _hr  # type: ignore
        cls._hr, cls._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        cls.home = home
        cls.cli = home / "fake-claude"
        cls.cli.write_text(FAKE_CLI.format(python=sys.executable))
        cls.cli.chmod(cls.cli.stat().st_mode | stat.S_IXUSR)
        os.environ["CORVIN_CLAUDE_BIN"] = str(cls.cli)

    @classmethod
    def tearDownClass(cls):
        cls._hr._house_rules_classifier = cls._hr_orig
        cls.tmp.cleanup()
        os.environ.clear()
        os.environ.update(cls._env_before)

    def _turn(self, commands):
        self.made = []
        real = bd.make_snap_dir
        def spy():
            d = real()
            self.made.append(d)
            return d
        bd.make_snap_dir = spy
        self.addCleanup(setattr, bd, "make_snap_dir", real)
        script = self.home / "script.json"
        script.write_text(json.dumps(commands))
        os.environ["FAKE_BASH_SCRIPT"] = str(script)
        sess = self.cr.create_session("_default")
        # commands name files relative to the session workdir (the stand-in's cwd)
        events = _drain(self.cr.stream_turn(sess, "change files"))
        return sess, events

    def _diffs(self, events):
        return {e["id"]: e for e in events if e.get("type") == "tool_diff"}

    def test_bash_change_reaches_the_browser_the_reload_and_not_the_worker(self):
        sess, events = self._turn([
            "printf 'a = 1\\nb = 2\\n' > f.py",
            "sed -i 's/b = 2/b = 20/' f.py",
            "ls",
        ])
        uses = [e for e in events if e.get("type") == "tool_use"]
        self.assertTrue(all("id" in u for u in uses))
        diffs = self._diffs(events)
        # the file creation and the in-place edit each show their change; `ls` shows none
        self.assertEqual(set(diffs), {"toolu_fake0", "toolu_fake1"})
        self.assertEqual(diffs["toolu_fake0"]["diff"].splitlines(), ["@@ f.py -0,0 +1,2 @@", "+a = 1", "+b = 2"])
        self.assertEqual(diffs["toolu_fake1"]["diff"].splitlines(),
                         ["@@ f.py -1,2 +1,2 @@", " a = 1", "-b = 2", "+b = 20"])
        # ordering: the card exists before its diff
        for tid, ev in diffs.items():
            use = next(i for i, e in enumerate(events) if e.get("type") == "tool_use" and e.get("id") == tid)
            self.assertLess(use, events.index(ev))
        # reload: persisted on the tool part, never as text
        turns = self.cr.read_turns("_default", sess.sid)
        parts = [p for t in turns if t.get("role") == "assistant" for p in t.get("parts") or []]
        persisted = {p["id"]: p for p in parts if p.get("kind") == "tool" and "diff" in p}
        self.assertEqual(set(persisted), {"toolu_fake0", "toolu_fake1"})
        self.assertNotIn("b = 20", json.dumps([p for p in parts if p.get("kind") == "text"]))
        import session_ledger  # type: ignore
        self.assertNotIn("b = 20", json.dumps(session_ledger.records_from_turn_log(turns)))
        # the per-turn snapshot dir is gone
        self.assertTrue(self.made)
        self.assertFalse(any(Path(d).exists() for d in self.made))

    def test_a_credential_in_the_change_withholds_the_whole_diff(self):
        _, events = self._turn(["printf 'API_KEY = \"sk-ant-api03-%s\"\\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa > cfg.py"])
        d = self._diffs(events)["toolu_fake0"]
        self.assertEqual(d.get("diff_withheld"), "credential")
        self.assertNotIn("sk-ant", json.dumps(events))

    def test_a_failed_command_still_shows_what_actually_changed(self):
        _, events = self._turn(["printf 'x\\n' > g.txt; exit 3"])
        lines = self._diffs(events)["toolu_fake0"]["diff"].splitlines()
        self.assertTrue(lines[0].startswith("@@ g.txt -0,0 +1"), lines)  # difflib omits ",1"
        self.assertEqual(lines[1:], ["+x"])

    def test_turn_budget_withholds_later_diffs(self):
        cr = self.cr
        cr._DIFF_TURN_BUDGET, saved = 40, cr._DIFF_TURN_BUDGET
        try:
            _, events = self._turn(["printf 'one\\ntwo\\n' > h1.txt", "printf 'three\\nfour\\n' > h2.txt"])
        finally:
            cr._DIFF_TURN_BUDGET = saved
        d = self._diffs(events)
        self.assertIn("diff", d["toolu_fake0"])
        self.assertEqual(d["toolu_fake1"].get("diff_withheld"), "turn_budget")


if __name__ == "__main__":
    unittest.main()
