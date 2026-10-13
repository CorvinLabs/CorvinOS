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


class _Sink:
    """A stdin that accepts every write (the flush unit under test)."""
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


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

    def test_the_client_is_told_when_the_steering_window_closes(self):
        _sess, events, _o = self._run(notes_expected=0, note_texts=())
        types = [e.get("type") for e in events]
        self.assertIn("btw_window_closed", types)
        self.assertLess(types.index("btw_window_closed"), types.index("done"))

    def test_an_expired_queued_note_is_recorded_undelivered_not_dropped(self):
        """Review #2: pruning at enqueue dropped an old queued note silently."""
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)
        try:
            with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", return_value=None):
                o1 = asyncio.run(cr.inject_btw_web(sess, "say ONE", client_id="exp00001"))
                cr._btw_pending[sess.chat_key][0]["ts"] -= cr.BTW_QUEUE_TTL_S + 5
                cr._btw_last_accept.pop(sess.chat_key, None)
                o2 = asyncio.run(cr.inject_btw_web(sess, "say TWO", client_id="exp00002"))
            self.assertEqual((o1.status, o2.status), ("queued", "queued"))
            written, rest = cr._btw_write_pending(_Sink(), sess.chat_key)
            self.assertEqual([i["client_id"] for i in written], ["exp00002"])
            self.assertEqual([i["client_id"] for i in rest], ["exp00001"])
            events = cr._btw_record(sess, written, "injected") + cr._btw_record(sess, rest, "undelivered")
            self.assertIn({"type": "btw_undelivered", "client_id": "exp00001"}, events)
            notes = {t["client_id"]: t["btw_status"] for t in cr.read_turns("_default", sess.sid) if t.get("btw")}
            self.assertEqual(notes, {"exp00001": "undelivered", "exp00002": "injected"})
        finally:
            cr._btw_turn_closed(sess.chat_key)

    def test_concurrent_notes_respect_idempotency_and_the_rate_limit(self):
        """Review #7: both checks were check-then-act across the gate's await."""
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)

        def slow_gate(text, **kw):
            import time as _t
            _t.sleep(0.2)
            return None

        async def go():
            same = await asyncio.gather(*[cr.inject_btw_web(sess, "say SAME", client_id="dup00001")
                                          for _ in range(3)])
            cr._btw_last_accept.pop(sess.chat_key, None)
            distinct = await asyncio.gather(*[cr.inject_btw_web(sess, f"say N{i}", client_id=f"dis0000{i}")
                                              for i in range(4)])
            return same, distinct
        try:
            with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", side_effect=slow_gate):
                same, distinct = asyncio.run(go())
            self.assertEqual({o.status for o in same}, {"queued"})
            self.assertEqual(len(cr._btw_pending[sess.chat_key]), 2)   # SAME once + one distinct
            self.assertEqual(sorted(o.status for o in distinct), ["queued", "refused", "refused", "refused"])
        finally:
            cr._btw_take_pending(sess.chat_key)
            cr._btw_turn_closed(sess.chat_key)

    def test_a_note_after_the_done_drain_is_no_active_stream_and_recorded(self):
        """Review #12/#3: accepted after the drain it waited for nobody; a
        no_active_stream note was not recorded and vanished on reload."""
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)
        try:
            cr._btw_drain_undelivered(sess)
            out = asyncio.run(cr.inject_btw_web(sess, "say AFTER", client_id="late0001"))
            self.assertEqual(out.status, "no_active_stream")
            note = [t for t in cr.read_turns("_default", sess.sid) if t.get("btw")][-1]
            self.assertEqual((note["btw_status"], note.get("btw_gated")), ("undelivered", None))
            import session_ledger  # type: ignore
            self.assertNotIn("AFTER", json.dumps(session_ledger.records_from_turn_log(
                cr.read_turns("_default", sess.sid))))
        finally:
            cr._btw_turn_closed(sess.chat_key)

    def test_a_cancel_during_the_turn_gate_marks_the_line_withheld(self):
        """Review #1: with the gate off the loop a cancel could land between the
        recorded user line and the refusal, and the ledger re-supplied text no
        gate had cleared."""
        import time as _time
        cr = self.cr
        sess = cr.create_session("_default")

        def slow_refusing_gate(text, **kw):
            _time.sleep(0.6)
            return "[house-rules] refused"

        async def go():
            async def consume():
                return [ev async for ev in cr.stream_turn(sess, "forbidden question")]
            task = asyncio.create_task(consume())
            await asyncio.sleep(0.3)          # the gate is deciding now
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.5)          # let the gate thread finish
        import contextlib
        with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal",
                               side_effect=slow_refusing_gate):
            asyncio.run(go())
        turns = cr.read_turns("_default", sess.sid)
        self.assertTrue(any(t.get("gate_refused") for t in turns), turns)
        import session_ledger  # type: ignore
        recs = session_ledger.records_from_turn_log(turns)
        self.assertTrue(all(r.get("refused") for r in recs if "forbidden" in r.get("user", "")), recs)

    # ── refutation round (review 2) ────────────────────────────────────────

    def test_a_note_whose_gate_outlives_its_turn_never_reaches_the_next_turn(self):
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)

        def slow_gate(text, **kw):
            import time as _t
            _t.sleep(0.4)
            return None

        async def go():
            task = asyncio.create_task(cr.inject_btw_web(sess, "say MEANT_FOR_N", client_id="xturn001"))
            await asyncio.sleep(0.1)
            cr._btw_drain_undelivered(sess)
            cr._btw_turn_closed(sess.chat_key)          # turn N ends …
            cr._btw_turn_opened(sess.chat_key)          # … turn N+1 starts while the gate runs
            return await task
        try:
            with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", side_effect=slow_gate):
                out = asyncio.run(go())
            self.assertEqual(out.status, "no_active_stream")
            self.assertEqual(cr._btw_pending.get(sess.chat_key, []), [])
        finally:
            cr._btw_take_pending(sess.chat_key)
            cr._btw_turn_closed(sess.chat_key)

    def test_summarizers_never_read_ungated_or_refused_text(self):
        cr = self.cr
        sess = cr.create_session("_default")
        cr._append_turn(sess, "user", [{"kind": "text", "text": "real question"}])
        cr._append_turn(sess, "user", cr._btw_note_parts("UNGATED-NOTE"), btw=True,
                        btw_status="refused", btw_reason="rate_limited")
        cr._append_turn(sess, "user", cr._btw_note_parts("DELIVERED-NOTE"), btw=True,
                        btw_status="injected", btw_gated=True)
        cr._append_turn(sess, "assistant", [{"kind": "text", "text": "the answer"}], cli_spawned=True)
        cr._append_turn(sess, "user", [{"kind": "text", "text": "REFUSED-QUESTION"}])
        cr._append_turn(sess, "assistant", [{"kind": "text", "text": "[house-rules] no"}],
                        gate_refused="pre_spawn")
        self.assertEqual(cr._last_exchange("_default", sess.sid), ("real question", "the answer"))
        from corvin_console.routes import voice as voice_routes
        transcript = voice_routes._build_session_transcript("_default", sess.sid)
        self.assertNotIn("UNGATED-NOTE", transcript)
        self.assertNotIn("REFUSED-QUESTION", transcript)
        self.assertIn("DELIVERED-NOTE", transcript)
        self.assertEqual(cr._first_user_text("_default", sess.sid), "real question")

    def test_ungated_records_are_capped(self):
        cr = self.cr
        sess = cr.create_session("_default")             # no open turn: every note is too late
        for i in range(cr.BTW_UNGATED_RECORD_MAX + 5):
            cr._btw_last_accept.pop(sess.chat_key, None)
            asyncio.run(cr.inject_btw_web(sess, f"say LATE{i}", client_id=f"cap{i:05d}"))
        notes = [t for t in cr.read_turns("_default", sess.sid) if t.get("btw")]
        self.assertEqual(len(notes), cr.BTW_UNGATED_RECORD_MAX)

    def test_a_note_that_was_not_accepted_gives_its_rate_slot_back(self):
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)
        try:
            with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal",
                                   side_effect=lambda t, **k: "[refused]" if "NO" in t else None):
                o1 = asyncio.run(cr.inject_btw_web(sess, "say NO", client_id="slot0001"))
                o2 = asyncio.run(cr.inject_btw_web(sess, "say YES", client_id="slot0002"))
            self.assertEqual((o1.status, o1.reason), ("refused", "gate"))
            self.assertEqual(o2.status, "queued")
        finally:
            cr._btw_take_pending(sess.chat_key)
            cr._btw_turn_closed(sess.chat_key)

    def test_expired_queued_notes_do_not_fill_the_queue(self):
        cr = self.cr
        sess = cr.create_session("_default")
        cr._btw_turn_opened(sess.chat_key)
        try:
            cr._btw_pending[sess.chat_key] = [
                {"text": f"old{i}", "via": "text", "ts": 0.0, "client_id": f"old{i:05d}"}
                for i in range(cr.BTW_QUEUE_MAX)]
            with mock.patch.object(cr._spawn_gates, "check_console_spawn_or_refusal", return_value=None):
                out = asyncio.run(cr.inject_btw_web(sess, "say FRESH", client_id="fresh001"))
            self.assertEqual(out.status, "queued")
        finally:
            cr._btw_take_pending(sess.chat_key)
            cr._btw_turn_closed(sess.chat_key)

    def test_a_stopped_gate_is_not_called_a_refusal_for_the_worker(self):
        import session_ledger  # type: ignore
        label = session_ledger._withheld({"refused": "pre_spawn_cancelled"})
        self.assertIn("stopped before its safety check finished", label)
        self.assertNotIn("refused by", label)

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
                self.assertEqual([(n["via"], n["btw_status"], n.get("btw_reason")) for n in note],
                                 [("voice", "injected", None), ("text", "refused", "rate_limited")])
                # the rate-limited note never passed a gate: never re-supplied
                from session_ledger import records_from_turn_log  # type: ignore
                self.assertNotIn("another", json.dumps(records_from_turn_log(
                    chat_runtime.read_turns(tenant_id, sid))))
                self.assertEqual(client.post(url, json={"instruction": "x", "via": "smoke"}).status_code, 422)
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)


if __name__ == "__main__":
    unittest.main()
