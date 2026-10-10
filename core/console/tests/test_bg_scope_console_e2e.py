"""E2E for T-0072 on the console web-chat (ADR-2236 D2-D7): ``chat_runtime.stream_turn``
against a REAL subprocess — the fake CLI replaying a stream captured from the real
claude CLI (``CORVIN_CLAUDE_BIN``) — must not treat the FIRST ``result`` as the end of
the turn when background children keep the process alive.

Asserted on the events the browser receives: interim results are marked and never
final, exactly one ``final`` result closes the turn and is what gets spoken, ``bg_status``
reports the open children, and the caps end a runaway scope with an honest message.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402


def _drain(agen):
    async def _collect():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.run(_collect())


@unittest.skipIf(sys.platform.startswith("win"), "POSIX process semantics in the harness")
class ConsoleBackgroundScopeE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(self.home / "corvin"),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "FORGE_ROOT": str(self.home / "forge"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(self.home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default",
            "CORVIN_OS_ENGINE": "claude_code",
        })
        os.environ.pop("VOICE_AUDIT_PATH", None)
        (self.home / "corvin").mkdir()
        from corvin_console import chat_runtime
        importlib.reload(chat_runtime)
        try:
            import forge.paths as fp  # type: ignore[import]
            importlib.reload(fp)
            importlib.reload(chat_runtime)
        except ImportError:
            pass
        self.cr = chat_runtime
        # The gates call the (fake) claude binary for their classifier; benign verdict.
        import house_rules as _hr  # type: ignore
        self._hr, self._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self._hr._house_rules_classifier = self._hr_orig
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self._env_before)

    def _turn(self, fixture, *, speedup=4.0, env=None, child=False):
        os.environ.update(kit.fake_env(self.home, fixture, speedup=speedup, child=child))
        os.environ.update(env or {})
        return _drain(self.cr.stream_turn(self.sess, "please do it"))

    @staticmethod
    def _results(events):
        return [e for e in events if e.get("type") == "result"]

    # ------------------------------------------------------------------------
    def test_first_result_is_interim_and_exactly_one_result_is_final(self):
        ev = self._turn("bash_bg_ok")
        res = self._results(ev)
        interim = [r for r in res if r.get("interim")]
        final = [r for r in res if r.get("final")]
        self.assertEqual(len(interim), 1, res)
        self.assertEqual(len(final), 1, res)
        self.assertIn("gestartet", interim[0]["text"])
        self.assertEqual(interim[0]["pending_children"], 1)
        self.assertFalse(interim[0]["annotation_pending"])
        self.assertIn("abgeschlossen", final[0]["text"].lower())
        self.assertEqual(final[0]["pending_children"], 0)
        # the closing message comes last, after every interim
        self.assertGreater(res.index(final[0]), res.index(interim[0]))
        # the voice key invariant: the LAST result event is what gets spoken
        self.assertIs(res[-1], final[0])

    def test_monitor_wakeups_are_interim_not_spoken_and_the_last_one_is_final(self):
        res = self._results(self._turn("monitor_3lines"))
        self.assertEqual([bool(r.get("interim")) for r in res], [True, True, True, True, False], res)
        self.assertTrue(res[-1]["final"] and "beendet" in res[-1]["text"].lower())
        self.assertEqual([r["pending_children"] for r in res[:4]], [1, 1, 1, 1])

    def test_bg_status_reports_open_children_with_only_a_scrubbed_label(self):
        ev = self._turn("bash_bg_ok")
        st = [e for e in ev if e.get("type") == "bg_status"]
        self.assertTrue(st, "no bg_status event")
        self.assertEqual(st[0]["open"], 1)
        self.assertEqual(st[-1]["open"], 0)
        child = st[0]["children"][0]
        # D9: the description reaches a status line only scrubbed and cut to 80 chars.
        self.assertEqual(set(child), {"id", "kind", "state", "age_s", "label"})
        self.assertEqual(child["kind"], "bash")
        self.assertTrue(child["label"].startswith("Sleep 8 seconds"), child["label"])
        self.assertLessEqual(len(child["label"]), 80)

    def test_bg_status_label_is_scrubbed_server_side(self):
        from corvin_console import chat_runtime as cr
        import bg_scope as bgs
        tr = bgs.ScopeTracker(scope_id="s")
        now = 1000.0
        tr.feed({"type": "system", "subtype": "task_started", "task_id": "t1", "task_type": "local_bash",
                 "description": "deploy to a.b@example.com with sk-ant-api03-ABCDEFGHIJKLMNOP and @everyone\nnow"},
                now=now)
        label = cr._bg_status_event(bgs, tr)["children"][0]["label"]
        for leaked in ("a.b@example.com", "sk-ant-api03", "\n"):
            self.assertNotIn(leaked, label)
        self.assertNotIn("@everyone", label)
        self.assertIn("deploy to", label)

    def test_turn_without_children_is_unchanged(self):
        ev = self._turn("plain_no_children")
        res = self._results(ev)
        self.assertEqual(len(res), 1)
        self.assertNotIn("interim", res[0])
        self.assertNotIn("final", res[0])
        self.assertFalse([e for e in ev if e.get("type") == "bg_status"])

    def test_the_turn_is_persisted_once_with_the_whole_conversation(self):
        ev = self._turn("monitor_3lines")
        text = "".join(e.get("text", "") for e in ev if e.get("type") == "delta")
        for needle in ("monitor laeuft", "Tick 1", "Tick 3", "Der Monitor ist beendet"):
            self.assertIn(needle, text)

    def test_child_cap_ends_the_scope_with_an_honest_message(self):
        ev = self._turn("bash_bg_ok", speedup=1, env={"CORVIN_BG_CHILD_MAX": "3"})
        res = self._results(ev)
        final = [r for r in res if r.get("final")]
        self.assertEqual(len(final), 1)
        self.assertIn("Stopped after 3 s", final[0]["text"])
        self.assertNotIn("abgeschlossen", final[0]["text"].lower())
        self.assertFalse(kit.pid_alive(self.home / "fake.pid"))
        self.assertFalse([e for e in ev if e.get("type") == "error"], ev)

    def test_wakeup_cap_stops_a_chatty_monitor(self):
        ev = self._turn("monitor_3lines", speedup=2, env={"CORVIN_BG_WAKEUP_MAX": "2"})
        final = [r for r in self._results(ev) if r.get("final")]
        self.assertEqual(len(final), 1)
        self.assertIn("more than 2 updates", final[0]["text"])
        self.assertFalse(kit.pid_alive(self.home / "fake.pid"))

    # ---- review round 2: the console must not orphan children, nor call a kill a finish ----

    def test_a_cap_ends_the_scope_with_SIGTERM_so_the_real_child_does_not_outlive_it(self):
        """Measured on the real CLI: SIGKILL orphans its background children, SIGTERM does not."""
        ev = self._turn("bash_bg_ok", speedup=1, env={"CORVIN_BG_CHILD_MAX": "3"}, child=True)
        final = [r for r in self._results(ev) if r.get("final")]
        self.assertEqual(len(final), 1)
        self.assertIn("Stopped after 3 s", final[0]["text"])
        self.assertFalse(kit.pid_alive(self.home / "fake.pid"))
        self.assertFalse(kit.pid_alive(self.home / "child.pid"), "the child outlived the cap (orphaned)")

    def test_the_process_ending_with_an_open_child_is_not_reported_as_a_clean_finish(self):
        events = kit.load_fixture("bash_bg_ok")
        cut = next(i for i, e in enumerate(events) if e.get("type") == "result")
        t = events[cut]["_t"]
        events = events[:cut + 1] + [{"type": "_eof", "_t": t + 0.6, "rc": 1}]
        fx = kit.write_fixture(self.home / "derived", "crash_open_child", events)
        ev = self._turn(fx)
        final = [r for r in self._results(ev) if r.get("final")]
        self.assertEqual(len(final), 1)
        self.assertIn("ended while 1 background task was still running", final[0]["text"])
        self.assertNotEqual(final[0]["text"].strip(), "gestartet")
        import forge.paths as fp  # type: ignore[import]
        import json
        recs = [json.loads(l) for l in fp.tenant_audit_chain("_default").read_text().splitlines()
                if '"bgscope.' in l]
        types = [r["event_type"] for r in recs]
        self.assertIn("bgscope.cancelled", types)
        self.assertEqual(types[-1], "bgscope.completed")
        self.assertEqual(recs[-1]["details"]["end_reason"], "process_died")

    def test_an_empty_last_wakeup_is_closed_with_a_deterministic_line(self):
        events = kit.load_fixture("bash_bg_ok")
        [e for e in events if e.get("type") == "result"][-1]["result"] = ""
        fx = kit.write_fixture(self.home / "derived", "empty_wakeup", events)
        final = [r for r in self._results(self._turn(fx)) if r.get("final")]
        self.assertEqual(final[0]["text"], "✅ Background work finished: 1 task done.")

    def test_scope_is_audited(self):
        self._turn("bash_bg_ok")
        import forge.paths as fp  # type: ignore[import]
        import json
        chain = fp.tenant_audit_chain("_default")
        types = [json.loads(l)["event_type"] for l in chain.read_text().splitlines()
                 if '"bgscope.' in l]
        self.assertEqual(types, ["bgscope.child_started", "bgscope.waiting",
                                 "bgscope.child_finished", "bgscope.completed"], types)


if __name__ == "__main__":
    unittest.main()
