"""Does a completed task ALWAYS get its persisted voice summary (Voice on)?

Scenario matrix, each through the real ``chat_runtime.stream_turn`` wrapper and the
real on-disk TaskManager / turns ledger; only the engine-spawning impl (or, in the
native class, the CLI binary) and the two paid subprocesses are substituted:

* completed turn, nobody listening ("inactive chat")      -> summary
* two chats complete in parallel                          -> one summary each
* success exit other than the native one (TDE / ACS)      -> summary
* consumer closes right at the last ``done`` (reload)     -> summary
* client vanishes MID-turn (reload / tab close)           -> task cancelled, NO summary
* Voice off                                               -> no task summary
* console restarts before the summary was produced        -> resumed at next connect
* summarizer fails twice, then works                      -> delivered; gives up after 3
"""
from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.chat_runtime as chat_runtime  # noqa: E402
import corvin_console.routes.chat as chat_routes  # noqa: E402
import corvin_console.routes.voice as voice_routes  # noqa: E402
from corvin_core.task_manager import TaskManager  # noqa: E402


def _completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout=stdout, stderr="")


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = f"test-{uuid.uuid4().hex[:12]}"
        gate = patch("corvin_console.routes._compute_license_gate.enforce_voice_summaries",
                     lambda *a, **k: None)
        gate.start()
        self.addCleanup(gate.stop)
        for name, value in (("_TASK_SUMMARY_SLOTS", None), ("_TASK_SUMMARY_RETRY_DELAY_S", 0.0)):
            old = getattr(chat_runtime, name)
            setattr(chat_runtime, name, value)
            self.addCleanup(setattr, chat_runtime, name, old)
        chat_runtime._OWED_SWEPT.discard(self.tenant_id)
        # The chat-level recap is a different pipeline with its own tests.
        recap = patch.object(voice_routes, "generate_and_persist_session_summary",
                             return_value=True)
        recap.start()
        self.addCleanup(recap.stop)

    def make_chat(self, *, voice_on: bool = True):
        sess = chat_runtime.create_session(self.tenant_id, title="Parallel")
        chat_runtime.note_voice_on(self.tenant_id, sess.sid, voice_on)
        return sess

    @staticmethod
    def impl(*, completes: bool, answer: str = "Das Ergebnis."):
        """A stand-in for _stream_turn_impl that does what every real exit does
        to the task lifecycle: create, start, then (maybe) record completion."""
        async def _impl(sess, prompt, *, sid_fingerprint=""):
            tasks_dir = sess.workdir / "tasks"
            tm = TaskManager(tasks_dir)
            task_id = tm.create_task(
                chat_key=sess.chat_key, instruction=prompt, persona="assistant",
                turn_number=sess.turn_count, tenant_id=sess.tenant_id)
            sess.in_flight_task = (tasks_dir, task_id)
            _impl.task_ids.append(task_id)
            tm.record_event(task_id, {"event": "task.started", "pid": 1})
            yield {"type": "delta", "text": "..."}
            if completes:
                chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": prompt}])
                chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": answer}])
                tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
            yield {"type": "done"}
        _impl.task_ids = []
        return _impl

    async def run_turn(self, sess, prompt: str, impl, *, close_after: int | None = None):
        """Consume stream_turn like the WS route does; optionally close the
        generator after *close_after* events (a client that went away)."""
        with patch.object(chat_runtime, "_stream_turn_impl", impl):
            async with contextlib.aclosing(chat_runtime.stream_turn(sess, prompt)) as gen:
                seen = 0
                async for _ in gen:
                    seen += 1
                    if close_after is not None and seen >= close_after:
                        break
        await self.settle()

    @staticmethod
    async def settle():
        pending = list(chat_runtime._TASK_SUMMARY_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class CompletionScenarios(_Base):
    def _recording_generate(self):
        calls: list[dict] = []

        def fake(tenant_id, sid, task_id, *, user_text, answer_text, **kw):
            calls.append({"sid": sid, "task_id": task_id, "user": user_text, "answer": answer_text})
            return True
        p = patch.object(voice_routes, "generate_and_persist_task_summary", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_completed_turn_with_nobody_listening_gets_its_summary(self) -> None:
        calls = self._recording_generate()
        sess = self.make_chat()
        impl = self.impl(completes=True, answer="Fertig: 3 Dateien.")
        asyncio.run(self.run_turn(sess, "Analysiere das", impl))
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0]["task_id"], impl.task_ids[0])
        self.assertEqual(calls[0]["answer"], "Fertig: 3 Dateien.")
        self.assertEqual(calls[0]["user"], "Analysiere das")
        # delivered -> no obligation left behind
        self.assertEqual(voice_routes.pending_task_summaries(self.tenant_id), [])

    def test_two_chats_completing_in_parallel_each_get_one(self) -> None:
        calls = self._recording_generate()
        a, b = self.make_chat(), self.make_chat()

        async def both():
            ia, ib = self.impl(completes=True, answer="A fertig"), self.impl(completes=True, answer="B fertig")
            await asyncio.gather(self.run_turn(a, "Task A", ia), self.run_turn(b, "Task B", ib))
        asyncio.run(both())
        by_sid = {c["sid"]: c for c in calls}
        self.assertEqual(set(by_sid), {a.sid, b.sid})
        self.assertEqual(by_sid[a.sid]["answer"], "A fertig")
        self.assertEqual(by_sid[b.sid]["answer"], "B fertig")

    def test_any_success_exit_is_covered_not_only_the_native_one(self) -> None:
        # TDE / ACS-delegated turns end elsewhere than the native block; the
        # wrapper decides on the recorded task status, so the exit is irrelevant.
        calls = self._recording_generate()
        sess = self.make_chat()
        asyncio.run(self.run_turn(sess, "delegiere das", self.impl(completes=True)))
        self.assertEqual(len(calls), 1)

    def test_consumer_closing_at_the_last_done_still_gets_the_summary(self) -> None:
        calls = self._recording_generate()
        sess = self.make_chat()
        # events: delta, done -> close while suspended at the final yield
        asyncio.run(self.run_turn(sess, "x", self.impl(completes=True), close_after=2))
        self.assertEqual(len(calls), 1)

    def test_client_vanishing_mid_turn_cancels_the_task_and_summarises_nothing(self) -> None:
        calls = self._recording_generate()
        sess = self.make_chat()
        impl = self.impl(completes=True)
        asyncio.run(self.run_turn(sess, "lange Aufgabe", impl, close_after=1))
        self.assertEqual(calls, [])
        task = TaskManager(sess.workdir / "tasks").get_task(impl.task_ids[0])
        self.assertEqual(task.status.value, "cancelled")
        self.assertEqual(voice_routes.pending_task_summaries(self.tenant_id), [])

    def test_voice_off_summarises_nothing(self) -> None:
        calls = self._recording_generate()
        sess = self.make_chat(voice_on=False)
        asyncio.run(self.run_turn(sess, "x", self.impl(completes=True)))
        self.assertEqual(calls, [])

    def test_voice_state_chosen_in_one_chat_is_not_lost_by_working_in_another(self) -> None:
        calls = self._recording_generate()
        first, second = self.make_chat(voice_on=True), self.make_chat(voice_on=False)
        asyncio.run(self.run_turn(second, "andere Aufgabe", self.impl(completes=True)))
        asyncio.run(self.run_turn(first, "erste Aufgabe", self.impl(completes=True)))
        self.assertEqual([c["sid"] for c in calls], [first.sid])


class DurabilityScenarios(_Base):
    def _synth(self, *, summarize_failures: int = 0):
        state = {"left": summarize_failures}

        def fake_run(cmd, **kwargs):
            if "--session-recap-mode" in cmd:
                if state["left"] > 0:
                    state["left"] -= 1
                    return _completed(1)
                return _completed(0, stdout="Kurzer Recap.\n")
            return _completed(0, stdout="ok")

        def fake_say_cmd(out_path, text, lang):
            Path(out_path).write_bytes(b"OggS" + b"0" * 40)
            return ["true"]
        for c in (patch.object(voice_routes.subprocess, "run", fake_run),
                  patch.object(voice_routes, "_say_cmd", fake_say_cmd)):
            c.start()
            self.addCleanup(c.stop)

    def _listing(self, sess):
        return voice_routes.list_task_summaries(self.tenant_id, sess.sid)

    def test_summary_is_retried_after_failures_and_then_delivered(self) -> None:
        self._synth(summarize_failures=2)
        sess = self.make_chat()
        asyncio.run(self.run_turn(sess, "x", self.impl(completes=True)))
        self.assertEqual(len(self._listing(sess)), 1)
        self.assertEqual(voice_routes.pending_task_summaries(self.tenant_id), [])

    def test_it_gives_up_after_the_last_attempt_and_audits_it(self) -> None:
        self._synth(summarize_failures=99)
        sess = self.make_chat()
        with patch.object(voice_routes.console_audit, "action_failed") as failed:
            asyncio.run(self.run_turn(sess, "x", self.impl(completes=True)))
        reasons = [c.kwargs["reason"] for c in failed.call_args_list]
        self.assertIn("gave-up-after-retries", reasons)
        self.assertEqual(self._listing(sess), [])
        self.assertEqual(voice_routes.pending_task_summaries(self.tenant_id), [])

    def test_a_restart_before_delivery_is_resumed_at_the_next_chat_connection(self) -> None:
        self._synth()
        sess = self.make_chat()
        # The process died after the task completed and the obligation was
        # recorded, before any summary existed.
        self.assertTrue(voice_routes.owe_task_summary(
            self.tenant_id, sess.sid, "tsk_orphan", user_text="Frage", answer_text="Antwort"))
        self.assertEqual(self._listing(sess), [])
        chat_runtime._OWED_SWEPT.discard(self.tenant_id)

        rec = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="fp")
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        app.include_router(voice_routes.router, prefix="/v1/console")
        with patch.object(chat_routes.session_auth, "load_session", return_value=rec):
            client = TestClient(app)
            client.cookies.set("corvin_console_sid", "valid-sid")
            with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                deadline = time.time() + 10
                while time.time() < deadline and not self._listing(sess):
                    time.sleep(0.1)
            body = client.get("/v1/console/voice/task-summaries",
                              params={"sid": sess.sid}).json()
        self.assertEqual([s["task_id"] for s in body["summaries"]], ["tsk_orphan"])
        self.assertEqual(voice_routes.pending_task_summaries(self.tenant_id), [])

    def test_the_sweep_runs_once_per_process_and_tenant(self) -> None:
        self._synth()
        sess = self.make_chat()

        async def go():
            voice_routes.owe_task_summary(self.tenant_id, sess.sid, "tsk_one",
                                          user_text="a", answer_text="b")
            first = chat_runtime.resume_owed_task_summaries(self.tenant_id)
            second = chat_runtime.resume_owed_task_summaries(self.tenant_id)
            await self.settle()
            return first, second
        self.assertEqual(asyncio.run(go()), (1, 0))

    def test_an_owed_task_already_running_is_not_started_twice(self) -> None:
        sess = self.make_chat()
        key = (self.tenant_id, sess.sid, "tsk_busy")
        chat_runtime._TASK_SUMMARY_INFLIGHT.add(key)
        self.addCleanup(chat_runtime._TASK_SUMMARY_INFLIGHT.discard, key)
        self.assertFalse(chat_runtime._spawn_owed_task_summary(*key))


if __name__ == "__main__":
    unittest.main()
