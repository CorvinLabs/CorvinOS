"""CONCEPT-0107 — /btw v2: the pre-spawn queue, its flush and `undelivered`.

Drives the REAL ``chat_runtime.stream_turn`` (the generator the chat
WebSocket consumes) against a stand-in ``claude`` binary (same harness as
``test_bash_diff.py``): the note is injected through the real
``inject_btw_web`` while the turn is open but its subprocess has not
registered stdin yet — the ~10 s pre-spawn window in which every note used to
be answered ``no_active_stream`` and lost. The route-level contract
(idempotency, rate limit, ``via``) goes through the real HTTP route.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

FAKE_CLI = textwrap.dedent('''\
    #!{python}
    """Stand-in for `claude -p --input-format stream-json`: one result per user line."""
    import json, os, sys
    def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    first = json.loads(sys.stdin.readline())
    emit({{"type": "system", "subtype": "init", "model": "claude-haiku-4-5"}})
    notes = [json.loads(sys.stdin.readline()) for _ in range(int(os.environ.get("FAKE_NOTES", "0")))]
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "main answer"}}]}}}})
    emit({{"type": "result", "subtype": "success", "result": "main answer", "is_error": False,
          "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
    for n in notes:
        t = "got " + n["message"]["content"]
        emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": t}}]}}}})
        emit({{"type": "result", "subtype": "success", "result": t, "is_error": False,
              "usage": {{"input_tokens": 1, "output_tokens": 1}}}})
''')


@unittest.skipIf(sys.platform.startswith("win"), "POSIX stand-in CLI")
class BtwQueueStreamE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        cls._env_before = dict(os.environ)
        os.environ.update({
            "CORVIN_HOME": str(home / "corvin"), "XDG_CONFIG_HOME": str(home / "xdg"),
            "CORVIN_AUDIT_ANCHOR_KEY": str(home / "anchor.key"),
            "CORVIN_TENANT_ID": "_default", "CORVIN_OS_ENGINE": "claude_code",
        })
        os.environ.pop("FORGE_ROOT", None)
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

    def _run(self, *, notes_expected: int, main_gate=None, note_texts=("say PINEAPPLE",)):
        """Run one turn; inject the notes at the first event the turn yields
        while its subprocess has not registered stdin yet (the generator is
        suspended there, exactly like a WebSocket consumer awaiting a send).
        Returns (sess, events, outcomes)."""
        cr = self.cr
        os.environ["FAKE_NOTES"] = str(notes_expected)
        sess = cr.create_session("_default")
        real_gate = cr._spawn_gates.check_console_spawn_or_refusal

        def gate(text, **kw):
            if text.startswith("say "):          # the notes themselves pass
                return None
            return main_gate(text) if main_gate else real_gate(text, **kw)

        async def go():
            events, outcomes = [], []
            async for ev in cr.stream_turn(sess, "main question"):
                events.append(ev)
                if (not outcomes and ev.get("type") != "done"
                        and sess.chat_key not in cr._running_stdins_web):
                    for i, t in enumerate(note_texts):
                        outcomes.append(await cr.inject_btw_web(
                            sess, t, via="voice", client_id=f"client{i:04d}"))
                        cr._btw_last_accept.pop(sess.chat_key, None)  # rate limit not under test
            return events, outcomes

        with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", side_effect=gate):
            events, outcomes = asyncio.run(go())
        return sess, events, outcomes

    def test_note_in_the_pre_spawn_window_is_queued_then_delivered(self):
        sess, events, outcomes = self._run(notes_expected=1)
        self.assertEqual([o.status for o in outcomes], ["queued"])
        flushed = [e for e in events if e.get("type") == "btw_flushed"]
        self.assertEqual(flushed, [{"type": "btw_flushed", "client_id": "client0000"}])
        # the model received it: the stand-in answers every user line
        text = json.dumps([e for e in events if e.get("type") in ("delta", "result")])
        self.assertIn("got", text)
        self.assertIn("PINEAPPLE", text)
        notes = [t for t in self.cr.read_turns("_default", sess.sid) if t.get("btw")]
        self.assertEqual([(n["btw_status"], n["via"], n["client_id"]) for n in notes],
                         [("injected", "voice", "client0000")])

    def test_queued_notes_keep_their_order(self):
        _, events, outcomes = self._run(notes_expected=2, note_texts=("say ONE", "say TWO"))
        self.assertEqual([o.status for o in outcomes], ["queued", "queued"])
        text = json.dumps([e.get("text") for e in events if e.get("type") == "result"])
        self.assertLess(text.index("ONE"), text.index("TWO"))

    def test_turn_that_never_spawns_reports_the_note_undelivered_before_done(self):
        sess, events, outcomes = self._run(
            notes_expected=0, main_gate=lambda _t: "[house-rules] refused for the test")
        self.assertEqual([o.status for o in outcomes], ["queued"])
        types = [e.get("type") for e in events]
        self.assertIn("btw_undelivered", types)
        self.assertLess(types.index("btw_undelivered"), types.index("done"))
        notes = [t for t in self.cr.read_turns("_default", sess.sid) if t.get("btw")]
        self.assertEqual([n["btw_status"] for n in notes], ["undelivered"])
        # the ledger keeps the words but never claims the model saw them
        import session_ledger  # type: ignore
        recs = session_ledger.records_from_turn_log(self.cr.read_turns("_default", sess.sid))
        note = next(r for r in recs if r["user"].startswith("/btw "))
        self.assertIn("NOT delivered", note["assistant"])

    def test_the_turn_gate_does_not_stall_the_event_loop(self):
        """The pre-spawn gate (L44 is a model call) ran inline on the loop: every
        other chat and request — a /btw included — waited for it (measured live:
        a note answered after 29 s). It must run off the loop."""
        import time as _time
        cr = self.cr
        os.environ["FAKE_NOTES"] = "0"
        sess = cr.create_session("_default")

        def slow_gate(text, **kw):
            _time.sleep(1.0)
            return None

        async def go():
            ticks = 0
            done = False
            async def ticker():
                nonlocal ticks
                while not done:
                    ticks += 1
                    await asyncio.sleep(0.05)
            t = asyncio.create_task(ticker())
            evs = [ev async for ev in cr.stream_turn(sess, "main question")]
            done = True
            await t
            return ticks, evs

        with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", side_effect=slow_gate):
            ticks, _evs = asyncio.run(go())
        self.assertGreaterEqual(ticks, 15, "the event loop stood still while the gate ran")

    def test_after_the_turn_closed_a_note_is_no_active_stream(self):
        sess, _events, _o = self._run(notes_expected=1)
        out = asyncio.run(self.cr.inject_btw_web(sess, "say LATE"))
        self.assertEqual(out.status, "no_active_stream")


class BtwRouteContractE2E(unittest.TestCase):
    """Idempotency, rate limit and `via` through the real HTTP route."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_idempotent_client_id_rate_limit_and_via(self):
        from test_chat_btw_inject_live_e2e import _FakeStdin
        from test_learning_loop_routes_e2e import _sandbox
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime
            sid = client.post("/v1/console/chat/sessions", json={"title": "btw v2"}).json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            try:
                url = f"/v1/console/chat/sessions/{sid}/btw"
                body = {"instruction": "shorter please", "via": "voice", "client_id": "abcdef0123"}
                r1 = client.post(url, json=body).json()
                r2 = client.post(url, json=body).json()          # the same note, retried
                self.assertEqual(r1, {"ok": True, "status": "injected", "client_id": "abcdef0123"})
                self.assertEqual(r2, r1)
                self.assertEqual(len(fake.writes), 1)              # written once
                r3 = client.post(url, json={"instruction": "another", "client_id": "zzzzzz9999"}).json()
                self.assertEqual((r3["status"], r3.get("reason")), ("refused", "rate_limited"))
                self.assertEqual(len(fake.writes), 1)
                note = [t for t in chat_runtime.read_turns(tenant_id, sid) if t.get("btw")]
                self.assertEqual([(n["via"], n["btw_status"]) for n in note], [("voice", "injected")])
                self.assertEqual(client.post(url, json={"instruction": "x", "via": "smoke"}).status_code, 422)
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)


if __name__ == "__main__":
    unittest.main()
