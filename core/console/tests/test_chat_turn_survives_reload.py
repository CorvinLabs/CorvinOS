"""A chat reload / closed tab / dropped link must not kill the running task.

Before, the WebSocket handler cancelled the turn the moment the client went
away: the task was recorded ``cancelled``, the answer was never persisted and —
with Voice on — no voice summary was ever produced, so a user who reloaded while
a task ran came back to nothing. Now the turn is detached and runs to
completion (bounded by ``CORVIN_DETACHED_TURN_MAX_S``).

The real WebSocket route and the real on-disk TaskManager/ledger are used. The
client is a ``TestClient`` *context*: its event loop outlives the socket, as the
server's does. (Without the context, leaving ``websocket_connect`` tears the loop
down and would cancel any task — which is why the older disconnect tests alone
cannot prove survival.)
"""
from __future__ import annotations

import asyncio
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


class ReloadSurvivalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = f"test-{uuid.uuid4().hex[:12]}"
        self.rec = MagicMock(tenant_id=self.tenant_id, sid_fingerprint="fp")
        for target, repl in (
            (patch.object(chat_routes.session_auth, "load_session", return_value=self.rec), None),
            (patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
                   lambda *a, **k: None), None),
            (patch.object(voice_routes, "generate_and_persist_session_summary",
                          return_value=True), None),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.summaries: list[str] = []
        gen = patch.object(
            voice_routes, "generate_and_persist_task_summary",
            side_effect=lambda t, s, task_id, **kw: self.summaries.append(task_id) or True)
        gen.start()
        self.addCleanup(gen.stop)
        self.sess = chat_runtime.create_session(self.tenant_id, title="Reload")
        chat_runtime.note_voice_on(self.tenant_id, self.sess.sid, True)
        self.task_ids: list[str] = []

    def _impl(self, *, run_s: float):
        outer = self

        async def impl(sess, prompt, *, sid_fingerprint=""):
            tasks_dir = sess.workdir / "tasks"
            tm = TaskManager(tasks_dir)
            task_id = tm.create_task(
                chat_key=sess.chat_key, instruction=prompt, persona="assistant",
                turn_number=sess.turn_count, tenant_id=sess.tenant_id)
            sess.in_flight_task = (tasks_dir, task_id)
            outer.task_ids.append(task_id)
            tm.record_event(task_id, {"event": "task.started", "pid": 1})
            yield {"type": "delta", "text": "arbeite..."}
            await asyncio.sleep(run_s)
            chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": prompt}])
            chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "Fertig nach Reload."}])
            tm.record_event(task_id, {"event": "task.completed", "exit_code": 0})
            yield {"type": "done"}
        return impl

    def _status(self) -> str | None:
        if not self.task_ids:
            return None
        task = TaskManager(self.sess.workdir / "tasks").get_task(self.task_ids[0])
        return task.status.value if task else None

    def _wait(self, predicate, timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def _disconnect_mid_turn(self, impl) -> None:
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        with patch.object(chat_runtime, "_stream_turn_impl", impl):
            # The client context outlives the socket, like the server's loop.
            with TestClient(app) as client:
                client.cookies.set("corvin_console_sid", "valid-sid")
                with client.websocket_connect(
                        f"/v1/console/chat/sessions/{self.sess.sid}/stream") as ws:
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    ws.send_json({"type": "user", "text": "lange Aufgabe", "voice_on": True})
                    self.assertEqual(ws.receive_json()["type"], "delta")
                # socket closed here = reload. The turn is still running.
                self.assertEqual(self._status(), "running")
                yield_state = self._wait(lambda: self._status() in ("completed", "cancelled"))
                self.assertTrue(yield_state, "task never reached a terminal status")
                # leave time for the detached summary to be spawned and run
                self._wait(lambda: bool(self.summaries) or self._status() == "cancelled", 3.0)

    def test_reload_mid_task_lets_the_task_complete_and_persist_its_answer(self) -> None:
        self._disconnect_mid_turn(self._impl(run_s=0.6))
        self.assertEqual(self._status(), "completed")
        turns = chat_runtime.read_turns(self.tenant_id, self.sess.sid)
        self.assertIn("Fertig nach Reload.", [chat_runtime._turn_text(t) for t in turns])

    def test_reload_mid_task_still_produces_the_voice_summary(self) -> None:
        self._disconnect_mid_turn(self._impl(run_s=0.6))
        self.assertEqual(self.summaries, self.task_ids)

    def test_a_detached_turn_is_bounded_by_the_wall_clock_limit(self) -> None:
        with patch.object(chat_routes, "_detached_turn_max_s", return_value=0.3):
            self._disconnect_mid_turn(self._impl(run_s=30.0))
        self.assertEqual(self._status(), "cancelled")
        self.assertEqual(self.summaries, [], "a cancelled task must not be summarised")

    def test_no_detached_turn_is_left_registered_afterwards(self) -> None:
        self._disconnect_mid_turn(self._impl(run_s=0.3))
        self.assertTrue(self._wait(lambda: not chat_routes._DETACHED_TURNS))


if __name__ == "__main__":
    unittest.main()
