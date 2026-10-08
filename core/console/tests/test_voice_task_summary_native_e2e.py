"""Native console turn -> persisted per-task voice summary, against a REAL
subprocess (the fake CLI replaying a stream captured from the real claude CLI,
via ``CORVIN_CLAUDE_BIN``) through the real ``stream_turn`` wrapper.

Only the paid summarize/TTS generation is recorded instead of run; what is
asserted is that the production turn path owes and starts the summary for the
task that really ran, with the answer the CLI really produced.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge"):
    sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402


@unittest.skipIf(sys.platform.startswith("win"), "POSIX process semantics in the harness")
class NativeTurnVoiceSummaryE2E(unittest.TestCase):
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
        import house_rules as _hr  # type: ignore
        self._hr, self._hr_orig = _hr, _hr._house_rules_classifier
        _hr._house_rules_classifier = lambda task, rules, auth, **kw: ("", 1.0, "test-benign")
        self.sess = self.cr.create_session("_default")

    def tearDown(self) -> None:
        self._hr._house_rules_classifier = self._hr_orig
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self._env_before)

    def _turn(self, *, voice_on: bool):
        import corvin_console.routes.voice as voice_routes
        calls: list[dict] = []

        def fake(tenant_id, sid, task_id, *, user_text, answer_text, **kw):
            calls.append({"sid": sid, "task_id": task_id, "user": user_text, "answer": answer_text})
            return True

        os.environ.update(kit.fake_env(self.home, "plain_no_children", speedup=4.0, child=False))
        self.cr.note_voice_on("_default", self.sess.sid, voice_on)

        async def go():
            events = []
            async for ev in self.cr.stream_turn(self.sess, "please do it"):
                events.append(ev)
            pending = list(self.cr._TASK_SUMMARY_TASKS)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return events

        with patch.object(voice_routes, "generate_and_persist_task_summary", side_effect=fake), \
                patch.object(voice_routes, "generate_and_persist_session_summary",
                             return_value=True):
            events = asyncio.run(go())
        return events, calls

    def test_a_real_native_turn_owes_and_delivers_its_summary(self):
        events, calls = self._turn(voice_on=True)
        answer = "".join(e.get("text", "") for e in events if e.get("type") == "result")
        self.assertTrue(answer.strip(), "the fake CLI produced no result")
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0]["sid"], self.sess.sid)
        self.assertEqual(calls[0]["user"], "please do it")
        self.assertTrue(calls[0]["answer"].strip(), "summary would be built from an empty answer")
        self.assertIn(calls[0]["answer"][:20], "".join(
            e.get("text", "") for e in events if e.get("type") in ("delta", "result")))
        from corvin_console.routes import voice as v
        self.assertEqual(v.pending_task_summaries("_default"), [])

    def test_voice_off_native_turn_owes_nothing(self):
        _events, calls = self._turn(voice_on=False)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
