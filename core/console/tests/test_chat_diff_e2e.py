"""E2E for ADR-2241: an executed Edit/Write shows Claude Code's own diff on its chat card.

``chat_runtime.stream_turn`` runs against a REAL subprocess (``CORVIN_CLAUDE_BIN``) that
replays a stream captured from the real claude CLI (``fixtures/chat_diff/real_file_tools.jsonl``,
claude-haiku-4-5, 2026-10-09): Edit ok, Edit failed (old_string not found), Write refused
(file not read), Write new file, Write a credential line, Read + Write over an existing file.

Asserted on what the browser receives (events), what a reload reads (turns.jsonl), what the
worker re-reads (the ledger records) and what the audit chain holds.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

FIXTURE = _THIS.parent / "fixtures" / "chat_diff" / "real_file_tools.jsonl"


def _drain(agen):
    async def _collect():
        return [ev async for ev in agen]
    return asyncio.run(_collect())


def _tool_ids() -> dict[str, str]:
    """name-with-target -> tool_use id, read from the capture itself."""
    out = {}
    for ln in FIXTURE.read_text().splitlines():
        d = json.loads(ln)
        if d.get("type") != "assistant":
            continue
        for b in d["message"]["content"]:
            if b.get("type") == "tool_use":
                target = Path(b["input"].get("file_path", "")).name
                key = f"{b['name']}:{target}"
                while key in out:
                    key += "'"
                out[key] = b["id"]
    return out


@unittest.skipIf(sys.platform.startswith("win"), "POSIX process semantics in the harness")
class ChatDiffE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        cls._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"),
            "XDG_CONFIG_HOME": str(home / "xdg"),
            "FORGE_ROOT": str(home / "forge"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
            "CORVIN_OS_ENGINE": "claude_code",
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
        cr = chat_runtime
        import house_rules as _hr  # type: ignore
        cls._hr, cls._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        cls.sess = cr.create_session("_default")
        os.environ.update(kit.fake_env(home, FIXTURE, speedup=20.0))
        cls.events = _drain(cr.stream_turn(cls.sess, "edit the files"))
        # The same stream with a per-turn diff budget that fits the first hunk only.
        cr._DIFF_TURN_BUDGET, budget = 60, cr._DIFF_TURN_BUDGET
        try:
            cls.budget_events = _drain(cr.stream_turn(cr.create_session("_default"), "edit the files"))
        finally:
            cr._DIFF_TURN_BUDGET = budget
        cls.turns = cr.read_turns("_default", cls.sess.sid)
        cls.turns_raw = "\n".join(p.read_text() for p in (home / "corvin").rglob("*.turns.jsonl"))
        cls.audit_raw = "\n".join(p.read_text() for p in home.rglob("audit.jsonl"))
        import session_ledger  # type: ignore
        cls.records = session_ledger.records_from_turn_log(cls.turns)
        cls.ids = _tool_ids()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._hr._house_rules_classifier = cls._hr_orig
        cls.tmp.cleanup()
        os.environ.clear()
        os.environ.update(cls._env_before)

    def _diff_event(self, key):
        tid = self.ids[key]
        got = [e for e in self.events if e.get("type") == "tool_diff" and e.get("id") == tid]
        self.assertLessEqual(len(got), 1)
        return got[0] if got else None

    def _persisted_part(self, key):
        tid = self.ids[key]
        parts = [p for t in self.turns if t.get("role") == "assistant" for p in t.get("parts") or []
                 if p.get("kind") == "tool" and p.get("id") == tid]
        self.assertEqual(len(parts), 1, f"{key} not persisted exactly once")
        return parts[0]

    # -- what the browser receives --------------------------------------------------
    def test_fixture_covers_the_cases(self):
        self.assertTrue({"Edit:f.py", "Edit:f.py'", "Write:w.txt", "Write:w.txt'",
                         "Write:new.txt", "Write:cfg.py", "Read:f.py"} <= set(self.ids), self.ids)

    def test_executed_edit_carries_the_clis_own_hunk(self):
        ev = self._diff_event("Edit:f.py")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["diff"].splitlines(), ["@@ -1,3 +1,3 @@", " a = 1", "-b = 2", "+b = 20", " c = 3"])
        self.assertFalse(ev["diff_truncated"])
        # the card the diff attaches to was announced first, with the same id
        use = [i for i, e in enumerate(self.events) if e.get("type") == "tool_use" and e.get("id") == ev["id"]]
        self.assertEqual(len(use), 1)
        self.assertLess(use[0], self.events.index(ev))

    def test_failed_and_refused_tools_show_no_change(self):
        self.assertIsNone(self._diff_event("Edit:f.py'"))     # old_string not found
        self.assertIsNone(self._diff_event("Write:w.txt"))    # refused: file not read yet
        self.assertNotIn("zzz_not_there", json.dumps(self.events))

    def test_overwrite_shows_removed_and_added_lines(self):
        lines = self._diff_event("Write:w.txt'")["diff"].splitlines()
        self.assertIn("-old line", lines)
        self.assertIn("+new line", lines)
        self.assertIn(" keep", lines)

    def test_new_file_is_all_added(self):
        self.assertEqual(self._diff_event("Write:new.txt")["diff"].splitlines(),
                         ["@@ -0,0 +1,2 @@", "+hello", "+world"])

    def test_credential_line_is_withheld_everywhere(self):
        ev = self._diff_event("Write:cfg.py")
        self.assertEqual(ev, {"type": "tool_diff", "id": self.ids["Write:cfg.py"], "diff_withheld": "credential"})
        self.assertNotIn("hunter2", json.dumps(self.events))
        self.assertNotIn("hunter2", self.turns_raw)

    def test_non_file_tools_get_no_id_and_no_diff(self):
        reads = [e for e in self.events if e.get("type") == "tool_use" and e.get("name") == "Read"]
        self.assertTrue(reads)
        self.assertTrue(all("id" not in e for e in reads))

    def test_per_turn_budget_withholds_once_spent(self):
        got = [e for e in self.budget_events if e.get("type") == "tool_diff"]
        self.assertEqual(got[0]["id"], self.ids["Edit:f.py"])
        self.assertIn("diff", got[0])                                   # 47 chars: fits
        by_id = {e["id"]: e for e in got}
        for key in ("Write:new.txt", "Write:w.txt'"):                  # would not fit any more
            self.assertEqual(by_id[self.ids[key]], {"type": "tool_diff", "id": self.ids[key],
                                                    "diff_withheld": "turn_budget"})
        self.assertEqual(by_id[self.ids["Write:cfg.py"]]["diff_withheld"], "credential")

    # -- what a reload reads ----------------------------------------------------------
    def test_reload_gets_the_same_diff(self):
        self.assertEqual(self._persisted_part("Edit:f.py")["diff"], self._diff_event("Edit:f.py")["diff"])
        self.assertEqual(self._persisted_part("Write:cfg.py").get("diff_withheld"), "credential")
        self.assertNotIn("diff", self._persisted_part("Edit:f.py'"))

    # -- what the worker and the audit chain see -------------------------------------
    def test_worker_history_and_audit_never_carry_the_diff(self):
        self.assertTrue(self.records)
        self.assertNotIn("b = 20", json.dumps(self.records))
        self.assertTrue(self.audit_raw, "no sandboxed audit chain was written")
        self.assertNotIn("b = 20", self.audit_raw)
        self.assertNotIn("hello", self.audit_raw)


if __name__ == "__main__":
    unittest.main()
