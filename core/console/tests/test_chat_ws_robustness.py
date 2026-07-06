"""Robustness regression for the chat WebSocket (routes/chat.py::chat_stream).

Locks the contract that a turn failure must NEVER drop the WebSocket — it
becomes an in-band error+done event and the socket stays open for the next
turn — and that client heartbeats are answered DURING a long turn (keepalive
through idle-killing proxies). Reproduces the "Connection lost" mid-tool-call
failure and proves it can't recur.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for p in ("core/console", "operator/bridges/shared", "operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.routes.chat as chat_routes  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app


async def _gen_raises(sess, prompt):  # noqa: ANN001
    """A turn that emits one tool_use then blows up (engine/tool/I-O hiccup)."""
    yield {"type": "tool_use", "name": "Read", "input": {}}
    raise RuntimeError("boom inside the engine turn")


async def _gen_slow(sess, prompt):  # noqa: ANN001
    """A turn with a long tool gap (no deltas) — exercises mid-turn keepalive."""
    yield {"type": "tool_use", "name": "Edit", "input": {}}
    await asyncio.sleep(0.4)
    yield {"type": "result", "text": "ok"}
    yield {"type": "done"}


class _SessStub:
    """Serializable session stub (MagicMock breaks _project's json.send)."""
    sid = "s1"
    chat_key = "web:s1"
    title = "Test"
    created_at = 0.0
    last_active_at = 0.0
    turn_count = 0
    workdir = None


class ChatWsRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = MagicMock()
        self.rec.tenant_id = "_default"
        self.sess = _SessStub()
        # ADR-0150 added a per-turn chat_turns_per_day charge to the chat WS. These
        # tests exercise WS streaming/robustness, not quota — neutralise the gate so
        # they are not blocked (and do not write a real quota counter).
        _qp = patch(
            "corvin_console.routes._compute_license_gate.enforce_chat_turns",
            lambda *a, **k: None,
        )
        _qp.start()
        self.addCleanup(_qp.stop)

    def _client(self) -> TestClient:
        c = TestClient(_app())
        c.cookies.set("corvin_console_sid", "valid-sid")
        return c

    def test_turn_exception_does_not_drop_socket(self) -> None:
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_raises),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "do it"})
                self.assertEqual(ws.receive_json()["type"], "tool_use")
                # The engine raised — must arrive as an in-band error, NOT a drop.
                self.assertEqual(ws.receive_json()["type"], "error")
                self.assertEqual(ws.receive_json()["type"], "done")
                # Socket MUST still be open: a ping is answered with a pong.
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_ping_during_turn_gets_pong_and_turn_completes(self) -> None:
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_slow),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s2/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "long task"})
                self.assertEqual(ws.receive_json()["type"], "tool_use")
                # Heartbeat sent while the turn is mid-flight (during the gap).
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")
                # Turn still finishes normally afterwards.
                self.assertEqual(ws.receive_json()["type"], "result")
                self.assertEqual(ws.receive_json()["type"], "done")

    def test_normal_turn_streams_and_socket_survives_for_next_turn(self) -> None:
        async def _gen_ok(sess, prompt):  # noqa: ANN001
            yield {"type": "delta", "text": "hi"}
            yield {"type": "result", "text": "hi"}
            yield {"type": "done"}

        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_ok),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s3/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": "one"})
                self.assertEqual(ws.receive_json()["type"], "delta")
                self.assertEqual(ws.receive_json()["type"], "result")
                self.assertEqual(ws.receive_json()["type"], "done")
                # Second turn on the SAME socket works (no reconnect needed).
                ws.send_json({"type": "user", "text": "two"})
                self.assertEqual(ws.receive_json()["type"], "delta")

    def test_non_object_json_does_not_drop_socket(self) -> None:
        """Adversarial review finding: a syntactically-valid, non-object JSON
        message (e.g. the bare text "42") parses fine but msg.get("type")
        raised an uncaught AttributeError, dropping the connection -- direct
        contradiction of this file's own documented "never drop the socket"
        contract. Must come back as an in-band error and keep the socket open."""
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s4/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_text("42")
                self.assertEqual(ws.receive_json()["type"], "error")
                # Socket MUST still be open afterwards.
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_browser_command_gate_import_does_not_nameerror(self) -> None:
        """Adversarial review finding: routes/chat.py referenced `_spawn_gates`
        in `/browser <task>` handling without ever importing it -- every call
        raised NameError, silently caught by the broad except and reported as
        "safety check failed", so the L44 gate never actually ran and the
        feature was entirely non-functional. Mock the gate itself (returning
        "permit") and the browser manager (raising an unrelated, expected
        failure) to prove the gate import resolves and actually executes,
        rather than the NameError short-circuit."""
        from corvin_console import _spawn_gates

        gate_calls = []

        def _fake_gate(*a, **k):
            gate_calls.append((a, k))
            return None  # permitted

        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(_spawn_gates, "check_console_spawn_or_refusal", _fake_gate),
            patch(
                "corvin_console.routes._compute_license_gate.enforce_chat_turns",
                lambda *a, **k: None,
            ),
        ):
            # browser._mgr() launches a real Playwright session -- stub it to
            # fail with an unrelated, expected error so this test stays fast
            # and hermetic while still proving we got PAST the gate.
            import corvin_console.routes.browser as browser_mod
            fake_mgr = MagicMock()
            fake_mgr.create = MagicMock(side_effect=RuntimeError("no browser in test env"))
            with patch.object(browser_mod, "_mgr", return_value=fake_mgr):
                c = self._client()
                with c.websocket_connect("/v1/console/chat/sessions/s5/stream") as ws:
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    ws.send_json({"type": "user", "text": "/browser go to example.com"})
                    resp = ws.receive_json()

        self.assertEqual(len(gate_calls), 1, "the L44 gate must actually run, not NameError")
        # Must be the browser-launch failure message, NOT the NameError
        # fallback's "safety check failed" text.
        self.assertIn("could not start browser", resp.get("message", ""))
        self.assertNotIn("safety check failed", resp.get("message", ""))


if __name__ == "__main__":
    unittest.main()
