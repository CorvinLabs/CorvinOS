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


async def _gen_raises(sess, prompt, **_kw):  # noqa: ANN001
    """A turn that emits one tool_use then blows up (engine/tool/I-O hiccup)."""
    yield {"type": "tool_use", "name": "Read", "input": {}}
    raise RuntimeError("boom inside the engine turn")


async def _gen_slow(sess, prompt, **_kw):  # noqa: ANN001
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
        async def _gen_ok(sess, prompt, **_kw):  # noqa: ANN001
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

    def test_coding_prompt_with_former_trigger_words_reaches_stream_turn_verbatim(self) -> None:
        """ADR-0193 Phase 2/3 regression: this is the user's ORIGINAL bug report
        ("baue mir eine Web-UI mit Login-Formular" used to get misclassified as
        a live-browsing task and lose the whole turn to a browser subprocess,
        because it contains "Login" -- one of the many trigger words the retired
        `_BROWSE_SIGNAL_RE`/`_classify_browser_intent` pre-gate matched on).
        There is no more pre-turn classification at all: EVERY prompt, however
        browsing-flavored its wording, reaches chat_runtime.stream_turn with its
        original text unchanged -- the model decides for itself, via ordinary
        tool-use reasoning over the native corvin-browser MCP tool, whether a
        browser action is actually needed."""
        received_prompts = []

        async def _gen_capture(sess, prompt, **_kw):  # noqa: ANN001
            received_prompts.append(prompt)
            yield {"type": "delta", "text": "ok, writing the component now"}
            yield {"type": "result", "text": "ok"}
            yield {"type": "done"}

        prompt_text = "baue mir eine Web-UI mit Login-Formular"
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=self.rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
            patch.object(chat_routes.chat_runtime, "stream_turn", _gen_capture),
        ):
            c = self._client()
            with c.websocket_connect("/v1/console/chat/sessions/s5/stream") as ws:
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "user", "text": prompt_text})
                self.assertEqual(ws.receive_json()["type"], "delta")
                self.assertEqual(ws.receive_json()["type"], "result")
                self.assertEqual(ws.receive_json()["type"], "done")

        self.assertEqual(received_prompts, [prompt_text])

    def test_retired_classifier_and_command_handler_names_stay_gone(self) -> None:
        """Locks in the Phase 2 removal itself: if any of these ever reappear
        on the module, a future edit silently reintroduced the classifier-
        routed side-channel this ADR retired."""
        retired_names = (
            "_BROWSE_SIGNAL_RE", "_classify_browser_intent", "_handle_browser_command",
            "_handle_browser_confirm_command", "_handle_browser_continue_command",
            "_detect_browser_task", "_extract_task_hosts", "_notify_browser_pause",
            "_URL_START_RE", "_BROWSE_INTENT_RE",
            "_BROWSER_CONFIRM_CMD_RE", "_BROWSER_CONTINUE_CMD_RE",
        )
        still_present = [n for n in retired_names if hasattr(chat_routes, n)]
        self.assertEqual(still_present, [])


if __name__ == "__main__":
    unittest.main()
