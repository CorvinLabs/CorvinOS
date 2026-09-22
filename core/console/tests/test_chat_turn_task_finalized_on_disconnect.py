"""An interrupted chat turn must leave a TERMINAL task, or the chat dies forever.

ADR-0080's per-chat quota counts ``status == "running"`` out of the on-disk task
meta, and nothing reconciles that field against reality — a task leaves
``running`` only when some code path records a terminal event. Every ordinary exit
of ``chat_runtime._stream_turn_impl`` records one (~25 call sites). An INTERRUPTED
turn did not: the three ``(CancelledError, GeneratorExit)`` handlers inside the
impl emit their paired ADR-0171 audit end and re-raise without touching the task
lifecycle, and an interruption before any of them (pre-spawn gates, model
resolution, context build) reached no handler at all.

So a browser refresh mid-turn leaked one permanently-``running`` task, and the
FIFTH leak hit ``max_concurrent=5`` and made every further turn on that chat raise
``QuotaExceededError`` — forever, surfacing only as the ``chat.py`` catch-all "The
turn failed unexpectedly (QuotaExceededError)". Measured on a live console
2026-09-21: one mid-turn disconnect, then ``running=1`` at t+30s with the event log
holding only ``['task.created', 'task.started']``.

The boot sweep (``corvin_plugins.bootstrap._reap_stale_tasks``, guarded by
``core/plugins/tests/test_stale_task_reaper_call_site.py``) is the other half: it
clears orphans a dead process left behind. It cannot help here, because THIS
console keeps running for days while the leak accumulates.

These tests drive the real WebSocket route and the real on-disk ``TaskManager``.
What they substitute is ``_stream_turn_impl`` — spawning a real ``claude`` engine
just to abandon it is not something a test can do — so the impl's own half of the
wiring (registering its task in ``sess.in_flight_task``) is asserted separately,
against the real source, by ``TestTheImplRegistersItsTask``.
"""
from __future__ import annotations

import asyncio
import ast
import json
import sys
import tempfile
import time
import unittest
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
from corvin_core.task_manager import TaskManager, TaskStatus  # noqa: E402

_TERMINAL_EVENTS = ("task.completed", "task.failed", "task.cancelled")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app


class _ChatTurnTaskTestBase(unittest.TestCase):
    """Real session + real task dir; only the engine-spawning impl is replaced."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sess = chat_runtime.WebChatSession(
            sid="s1",
            tenant_id="_default",
            created_at=0.0,
            last_active_at=0.0,
            workdir=Path(self._tmp.name) / "web_s1",
        )
        self.sess.workdir.mkdir(parents=True)
        self.tasks_dir = self.sess.workdir / "tasks"
        self.rec = MagicMock()
        self.rec.tenant_id = "_default"
        self.rec.sid_fingerprint = "fp"
        # ADR-0150 per-turn chat quota: not what these tests are about.
        _qp = patch(
            "corvin_console.routes._compute_license_gate.enforce_chat_turns",
            lambda *a, **k: None,
        )
        _qp.start()
        self.addCleanup(_qp.stop)

    def tm(self) -> TaskManager:
        return TaskManager(self.tasks_dir)

    def _start_real_task(self, sess, *, instruction: str = "a long turn") -> str:
        """Do exactly what ``_stream_turn_impl`` does up to ``task.started``."""
        tasks_dir = sess.workdir / "tasks"
        tm = TaskManager(tasks_dir)
        task_id = tm.create_task(
            chat_key=sess.chat_key,
            instruction=instruction,
            persona="assistant",
            turn_number=sess.turn_count,
            tenant_id=sess.tenant_id,
        )
        sess.in_flight_task = (tasks_dir, task_id)
        tm.record_event(task_id, {"event": "task.started", "pid": 424242})
        return task_id

    def _events(self, task_id: str) -> list[str]:
        log = self.tasks_dir / f"{task_id}.events.jsonl"
        return [
            json.loads(line)["event"]
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _client(self) -> TestClient:
        c = TestClient(_app())
        c.cookies.set("corvin_console_sid", "valid-sid")
        return c

    def _run_turn_then(self, impl, *, disconnect_mid_turn: bool):
        """Drive one real WS turn against ``impl``; return the task_id it made."""
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_runtime, "_stream_turn_impl", impl),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "count slowly to forty"})
                # The impl has begun and created its task.
                self.assertEqual(ws.receive_json()["type"], "delta")
                if not disconnect_mid_turn:
                    while ws.receive_json()["type"] != "done":
                        pass
            # Leaving the context manager closes the socket. Mid-turn that is the
            # reported failure mode: a refreshed/closed browser tab.
        return self._await_terminal()

    def _await_terminal(self, timeout: float = 5.0) -> str:
        """The disconnect is handled on the server's own task; give it a moment."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            metas = [p for p in self.tasks_dir.glob("*.json") if not p.name.endswith(".tmp")]
            if metas:
                last = metas[0]
                status = json.loads(last.read_text(encoding="utf-8"))["status"]
                if status not in ("running", "pending"):
                    return last.stem
            time.sleep(0.05)
        self.fail(
            "the task never reached a terminal status — an interrupted turn leaked a "
            f"permanently-running task (meta={last})"
        )
        raise AssertionError  # unreachable, keeps type checkers happy


class TestAnInterruptedTurnIsFinalized(_ChatTurnTaskTestBase):
    def test_mid_turn_disconnect_finalizes_the_task_and_frees_the_quota(self) -> None:
        """The reported bug, end to end over the real WebSocket."""
        base = self

        async def _impl(sess, prompt, **_kw):  # noqa: ANN001
            base._start_real_task(sess)
            yield {"type": "delta", "text": "1\n"}
            await asyncio.sleep(30)  # still streaming when the client vanishes
            yield {"type": "done"}

        task_id = self._run_turn_then(_impl, disconnect_mid_turn=True)

        tm = self.tm()
        task = tm.get_task(task_id)
        self.assertEqual(
            task.status, TaskStatus.CANCELLED,
            "an abandoned turn is cancelled, not failed — it says nothing about "
            "whether the engine did well (and so emits no ADR-0314 outcome)",
        )
        self.assertEqual(
            tm._count_running_tasks(self.sess.chat_key), 0,
            "freeing the quota counter is the entire point of finalizing",
        )
        # Append-only: the interruption is recorded, nothing is erased.
        self.assertEqual(
            self._events(task_id),
            ["task.created", "task.started", "task.cancelled"],
        )
        self.assertIsNone(
            self.sess.in_flight_task,
            "the slot must be cleared, or the next turn's finalizer would look at "
            "a task that is already over",
        )

    def test_five_disconnects_do_not_kill_the_chat(self) -> None:
        """The actual user-visible failure: the FIFTH leak, not the first.

        Without finalization these five turns reach ``max_concurrent`` and every
        further turn raises ``QuotaExceededError`` for the lifetime of the chat.
        """
        base = self

        async def _impl(sess, prompt, **_kw):  # noqa: ANN001
            base._start_real_task(sess)
            yield {"type": "delta", "text": "working\n"}
            await asyncio.sleep(30)
            yield {"type": "done"}

        for _ in range(5):
            self._run_turn_then(_impl, disconnect_mid_turn=True)

        tm = self.tm()
        self.assertEqual(tm._count_running_tasks(self.sess.chat_key), 0)
        # Green: the chat still accepts turns. This is the assertion that was red.
        tm.check_quota(self.sess.chat_key, max_concurrent=5)

    def test_a_normally_completed_turn_gets_no_second_terminal_event(self) -> None:
        """Idempotence, the property the whole design rests on.

        The finalizer runs on EVERY turn, including successful ones. If it wrote
        unconditionally it would double-count the turn and re-emit its ADR-0314
        outcome, which is worse than the leak it fixes.
        """
        base = self

        async def _impl(sess, prompt, **_kw):  # noqa: ANN001
            task_id = base._start_real_task(sess, instruction="a normal turn")
            yield {"type": "delta", "text": "hi"}
            TaskManager(sess.workdir / "tasks").record_event(
                task_id, {"event": "task.completed", "exit_code": 0})
            yield {"type": "done"}

        task_id = self._run_turn_then(_impl, disconnect_mid_turn=False)

        self.assertEqual(self.tm().get_task(task_id).status, TaskStatus.COMPLETED)
        events = self._events(task_id)
        self.assertEqual(
            [e for e in events if e in _TERMINAL_EVENTS], ["task.completed"],
            f"exactly one terminal event per turn; got {events}",
        )

    def test_an_exploding_turn_is_finalized_too(self) -> None:
        """A turn that raises before writing a terminal event leaks the same way.

        ``routes/chat.py`` converts the exception into an in-band error+done and
        keeps the socket open (test_chat_ws_robustness), which is right for the
        user and used to leave the task on ``running`` regardless.
        """
        base = self

        async def _impl(sess, prompt, **_kw):  # noqa: ANN001
            base._start_real_task(sess, instruction="a doomed turn")
            yield {"type": "delta", "text": "hi"}
            raise RuntimeError("boom inside the engine turn")

        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_runtime, "_stream_turn_impl", _impl),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "do it"})
                self.assertEqual(ws.receive_json()["type"], "delta")
                self.assertEqual(ws.receive_json()["type"], "error")
                self.assertEqual(ws.receive_json()["type"], "done")

        task_id = self._await_terminal()
        self.assertEqual(self.tm().get_task(task_id).status, TaskStatus.CANCELLED)
        self.assertEqual(self.tm()._count_running_tasks(self.sess.chat_key), 0)


class TestQuotaExhaustionIsReportedAsItself(_ChatTurnTaskTestBase):
    def test_the_user_is_told_the_chat_is_busy_not_that_something_broke(self) -> None:
        """The wording is what sent this investigation the wrong way.

        ``QuotaExceededError`` is a designed, actionable condition; the catch-all
        called it "unexpected" and pointed at the server logs, so the operator
        looked for a crash instead of for a full chat. The exception text is a bare
        count ("5 tasks running, max 5") — no user content, safe to show.
        """
        async def _impl(sess, prompt, **_kw):  # noqa: ANN001
            raise chat_runtime._task_manager.QuotaExceededError(
                "Quota exceeded: 5 tasks running, max 5")
            yield  # pragma: no cover — makes this an async generator

        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_runtime, "_stream_turn_impl", _impl),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "one more turn"})
                err = ws.receive_json()
                self.assertEqual(err["type"], "error")
                msg = err["message"]
                self.assertNotIn("unexpected", msg.lower(), msg)
                self.assertIn("5 tasks running, max 5", msg)
                self.assertIn("new chat", msg.lower())
                self.assertEqual(ws.receive_json()["type"], "done")
                # And the socket survives, as for every other turn failure.
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")


class TestTheFinalizerIsSafeToCallAlways(unittest.TestCase):
    """It runs in a ``finally`` during cancellation, on every single turn."""

    def _sess(self, workdir: Path) -> chat_runtime.WebChatSession:
        return chat_runtime.WebChatSession(
            sid="s9", tenant_id="_default", created_at=0.0,
            last_active_at=0.0, workdir=workdir,
        )

    def test_no_task_in_flight_is_a_no_op(self) -> None:
        sess = self._sess(Path("/nonexistent"))
        self.assertIsNone(chat_runtime._finalize_in_flight_task(sess))

    def test_an_unreadable_task_dir_does_not_raise(self) -> None:
        """Best-effort by contract: bookkeeping must never break a turn — the
        finalizer's exception would replace the user's result with a crash."""
        sess = self._sess(Path("/nonexistent"))
        sess.in_flight_task = (Path("/nonexistent/tasks"), "no-such-task")
        self.assertIsNone(chat_runtime._finalize_in_flight_task(sess))
        self.assertIsNone(sess.in_flight_task, "the slot is cleared regardless")

    def test_it_is_synchronous(self) -> None:
        """An ``await`` in there would re-raise CancelledError on the very path
        this exists to serve, skipping the write entirely."""
        import inspect

        self.assertFalse(
            inspect.iscoroutinefunction(chat_runtime._finalize_in_flight_task))
        self.assertFalse(
            inspect.isasyncgenfunction(chat_runtime._finalize_in_flight_task))


class TestTheImplRegistersItsTask(unittest.TestCase):
    """The half the WS tests above substitute, asserted against the real source.

    ``stream_turn``'s finalizer can only act on what ``_stream_turn_impl`` put in
    ``sess.in_flight_task``. If that assignment is ever dropped — or drifts away
    from the ``create_task`` call it belongs to — the finalizer silently becomes a
    no-op and the leak returns with every test above still green.
    """

    def _impl_ast(self) -> ast.FunctionDef:
        src = Path(chat_runtime.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_stream_turn_impl"
        )

    def test_the_impl_assigns_sess_in_flight_task(self) -> None:
        assigns = [
            n for n in ast.walk(self._impl_ast())
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Attribute) and t.attr == "in_flight_task"
        ]
        self.assertEqual(
            len(assigns), 1,
            "_stream_turn_impl must register its task in sess.in_flight_task "
            "exactly once — without it stream_turn's finalizer has nothing to "
            "finalize and an interrupted turn leaks a running task again",
        )

    def test_registration_immediately_follows_create_task(self) -> None:
        """Coupling, not mere presence: a task created and registered far apart
        leaves a window in which an interruption still leaks."""
        impl = self._impl_ast()
        create = [
            n.lineno for n in ast.walk(impl)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "create_task"
        ]
        register = [
            n.lineno for n in ast.walk(impl)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Attribute) and t.attr == "in_flight_task"
        ]
        self.assertTrue(create, "no create_task call in _stream_turn_impl")
        self.assertTrue(register, "no in_flight_task registration in _stream_turn_impl")
        gap = register[0] - max(c for c in create if c < register[0])
        self.assertLessEqual(
            gap, 12,
            f"the registration sits {gap} lines after create_task; keep them "
            "adjacent so no interruption can land between them",
        )

    def test_the_public_entry_point_is_the_wrapper(self) -> None:
        """``routes/chat.py`` calls ``chat_runtime.stream_turn``. If that name
        ever points back at the raw impl, the finally clause disappears with it.
        """
        src = Path(chat_runtime.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        wrapper = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_turn"
        )
        called = {
            n.func.id for n in ast.walk(wrapper)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        self.assertIn("_stream_turn_impl", called)
        self.assertIn("_finalize_in_flight_task", called)
        self.assertTrue(
            any(isinstance(n, ast.Try) and n.finalbody for n in ast.walk(wrapper)),
            "the finalizer must run from a finally block — an interrupted turn "
            "never reaches ordinary statements after the yield loop",
        )


if __name__ == "__main__":
    unittest.main()
