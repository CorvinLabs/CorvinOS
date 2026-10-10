"""A socket that connects while a chat turn runs receives that turn (live_turns.py).

Measured 2026-10-09: a console_auto_reload redeploy reloaded the operator's tab
eight seconds into a 95-second turn. The turn finished and was persisted, but
its events only ever went to the WebSocket that started it — the reloaded tab's
new socket got nothing, and the chat showed no agent output at all.

These tests drive the real WebSocket route (Starlette TestClient: every
``websocket_connect`` runs on its own event-loop thread, so the hub's
cross-loop delivery is exercised too). Only ``_stream_turn_impl`` — the engine
spawn — is replaced; the hub, the route wiring and the replay are real.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
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
import corvin_console.live_turns as live_turns  # noqa: E402
import corvin_console.routes.chat as chat_routes  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app


def _receive_until(ws, predicate, limit: int = 50) -> list[dict]:
    out: list[dict] = []
    for _ in range(limit):
        evt = ws.receive_json()
        out.append(evt)
        if predicate(evt):
            return out
    raise AssertionError(f"predicate never matched; got {out}")


class TestLiveTurnAttach(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sess = chat_runtime.WebChatSession(
            sid="s1", tenant_id="_default", created_at=0.0, last_active_at=0.0,
            workdir=Path(self._tmp.name) / "web_s1",
        )
        self.sess.workdir.mkdir(parents=True)
        self.rec = MagicMock()
        self.rec.tenant_id = "_default"
        self.rec.sid_fingerprint = "fp"
        for target, value in (
            ("corvin_console.routes._compute_license_gate.enforce_chat_turns", lambda *a, **k: None),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        # The turn proceeds step by step under the test's control.
        self.step = threading.Event()
        self.finish = threading.Event()
        self.addCleanup(self._release)

    async def _wait(self, ev: threading.Event, limit_s: float = 10.0) -> None:
        # Bounded: a failing assertion must turn the test red, never hang the
        # TestClient teardown waiting on a turn nobody will release.
        for _ in range(int(limit_s / 0.02)):
            if ev.is_set():
                return
            await asyncio.sleep(0.02)

    def _release(self) -> None:
        self.step.set()
        self.finish.set()

    def _impl(self):
        base = self

        async def impl(sess, prompt, **_kw):  # noqa: ANN001
            yield {"type": "delta", "text": "Teil 1. "}
            yield {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
            await base._wait(base.step)
            yield {"type": "delta", "text": "Teil 2."}
            await base._wait(base.finish)
            yield {"type": "result", "text": "Teil 1. Teil 2."}
            yield {"type": "done"}

        return impl

    def _patches(self):
        return (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_runtime, "_stream_turn_impl", self._impl()),
        )

    def test_a_socket_connecting_mid_turn_gets_replay_live_events_and_an_attached_done(self) -> None:
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            c = TestClient(_app())
            c.cookies.set("corvin_console_sid", "valid-sid")
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as owner:
                self.assertEqual(owner.receive_json()["type"], "ready")
                owner.send_json({"type": "user", "text": "eine lange Aufgabe"})
                first = _receive_until(owner, lambda e: e["type"] == "tool_use")
                started = [e for e in first if e["type"] == "turn_started"]
                self.assertEqual(len(started), 1, first)
                turn_id = started[0]["turn_id"]

                # The reloaded tab: a NEW socket while the turn runs.
                with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as late:
                    self.assertEqual(late.receive_json()["type"], "ready")
                    # Ping first: the snapshot is sent on connect, so it must
                    # precede the pong. (A server without the hub answers only
                    # the pong — the test fails instead of blocking forever.)
                    late.send_json({"type": "ping"})
                    attach = late.receive_json()
                    self.assertEqual(attach["type"], "turn_attach",
                                     "a socket connecting mid-turn was not attached to the turn")
                    self.assertEqual(attach["turn_id"], turn_id)
                    self.assertFalse(attach["truncated"])
                    # Replay of everything before the attach, in order.
                    self.assertEqual(late.receive_json(), {"type": "delta", "text": "Teil 1. "})
                    self.assertEqual(late.receive_json()["type"], "tool_use")

                    # Live: produced after the late socket attached.
                    self.assertEqual(late.receive_json()["type"], "pong")
                    self.step.set()
                    self.assertEqual(late.receive_json(), {"type": "delta", "text": "Teil 2."})
                    self.assertEqual(
                        _receive_until(owner, lambda e: e["type"] == "delta")[-1]["text"], "Teil 2.",
                    )

                    self.finish.set()
                    tail = _receive_until(late, lambda e: e["type"] == "done")
                    self.assertEqual(tail[-1], {"type": "done", "attached": True, "turn_id": turn_id})
                    self.assertIn({"type": "result", "text": "Teil 1. Teil 2."}, tail)

                # The owner sees its own turn exactly once (no fan-out echo).
                owner_tail = _receive_until(owner, lambda e: e["type"] == "done")
                self.assertNotIn("attached", owner_tail[-1])
                self.assertEqual(sum(1 for e in owner_tail if e["type"] == "result"), 1)

        self.assertIsNone(live_turns.live_turn_id("_default", "s1"), "the hub forgets an ended turn")

    def test_a_socket_connecting_after_the_turn_gets_no_attach(self) -> None:
        self.step.set()
        self.finish.set()
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            c = TestClient(_app())
            c.cookies.set("corvin_console_sid", "valid-sid")
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as owner:
                owner.receive_json()
                owner.send_json({"type": "user", "text": "kurz"})
                _receive_until(owner, lambda e: e["type"] == "done")
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as later:
                self.assertEqual(later.receive_json()["type"], "ready")
                later.send_json({"type": "ping"})
                self.assertEqual(later.receive_json()["type"], "pong",
                                 "no turn_attach for a turn that is over")

    def test_stop_from_the_attached_tab_stops_the_turn(self) -> None:
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            c = TestClient(_app())
            c.cookies.set("corvin_console_sid", "valid-sid")
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as owner:
                owner.receive_json()
                owner.send_json({"type": "user", "text": "lang"})
                _receive_until(owner, lambda e: e["type"] == "tool_use")
                with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as late:
                    late.receive_json()  # ready
                    late.send_json({"type": "ping"})
                    self.assertEqual(late.receive_json()["type"], "turn_attach")
                    _receive_until(late, lambda e: e["type"] == "pong")  # replay, then the pong
                    late.send_json({"type": "cancel"})
                    events = _receive_until(late, lambda e: e["type"] == "done")
                    self.assertEqual(events[-1].get("attached"), True)
                    self.assertTrue(any(e.get("type") == "info" and "stopping" in e.get("message", "")
                                        for e in events), events)
                owner_tail = _receive_until(owner, lambda e: e["type"] == "done")
                self.assertFalse(
                    any(e.get("message") == "This chat was deleted." for e in owner_tail),
                    "a stop from another tab is not a deletion",
                )


class TestHubBounds(unittest.TestCase):
    def test_replay_buffer_is_bounded_and_marks_truncation(self) -> None:
        async def run() -> dict:
            tid = live_turns.begin("_t", "sb", "owner")
            big = {"type": "delta", "text": "x" * 1024}
            with patch.object(live_turns, "MAX_BUFFER_BYTES", 10 * 1024):
                for _ in range(50):
                    live_turns.publish("_t", "sb", tid, big)
                snap, _q, unsub = live_turns.subscribe("_t", "sb", "late")
            unsub()
            live_turns.end("_t", "sb", tid)
            return {"attach": snap[0], "n": len(snap) - 1}

        out = asyncio.run(run())
        self.assertTrue(out["attach"]["truncated"])
        self.assertLess(out["n"], 50)

    def test_a_stale_owner_cannot_publish_into_a_newer_turn(self) -> None:
        async def run() -> list:
            old = live_turns.begin("_t", "sc", "o1")
            new = live_turns.begin("_t", "sc", "o2")
            live_turns.publish("_t", "sc", old, {"type": "delta", "text": "stale"})
            live_turns.end("_t", "sc", old)  # no-op: not the live turn
            snap, _q, unsub = live_turns.subscribe("_t", "sc", "late")
            unsub()
            live_turns.end("_t", "sc", new)
            return snap

        snap = asyncio.run(run())
        self.assertEqual([e["type"] for e in snap], ["turn_attach"])

    def test_a_subscriber_that_stops_reading_gets_one_resync_marker(self) -> None:
        async def run() -> list:
            tid = live_turns.begin("_t", "sd", "owner")
            with patch.object(live_turns, "MAX_QUEUE", 3):
                _snap, q, unsub = live_turns.subscribe("_t", "sd", "slow")
            for i in range(10):
                live_turns.publish("_t", "sd", tid, {"type": "delta", "text": str(i)})
            items = []
            while not q.empty():
                items.append(q.get_nowait())
            unsub()
            live_turns.end("_t", "sd", tid)
            return items

        items = asyncio.run(run())
        self.assertEqual(items[-1], {"type": "turn_resync"})
        self.assertEqual(sum(1 for e in items if e["type"] == "turn_resync"), 1)


if __name__ == "__main__":
    unittest.main()
