"""The chat WebSocket closes with a code the browser can see (routes/chat.py::chat_stream).

A ``close()`` BEFORE ``accept()`` is an HTTP 403 on the handshake; the browser reports
that as 1006 and never sees 4401/4404, so chat-registry.ts's "Session expired" /
"Chat session not found" branches were unreachable and it reconnected every 30 s
forever (2026-11-07: 52 handshakes answered 403 for one chat the 50-per-tenant cap had
removed). Now the socket is accepted and closed with the code; nothing else is sent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import corvin_console.routes.chat as chat_routes  # noqa: E402


def _client(cookie: str | None) -> TestClient:
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    c = TestClient(app)
    if cookie:
        c.cookies.set("corvin_console_sid", cookie)
    return c


def _close_code(c: TestClient) -> int:
    with c.websocket_connect("/v1/console/chat/sessions/s1/stream") as ws:
        with self_assert_raises(WebSocketDisconnect) as cm:
            ws.receive_json()
    return cm.exception.code


class self_assert_raises:  # tiny helper so _close_code stays a function
    def __init__(self, exc):
        self.exc = exc
    def __enter__(self):
        return self
    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError("socket did not close")
        if not issubclass(et, self.exc):
            return False
        self.exception = ev
        return True


class ChatWsCloseCodeTests(unittest.TestCase):
    def test_no_cookie_closes_4401_after_accept(self) -> None:
        self.assertEqual(_close_code(_client(None)), 4401)

    def test_unknown_session_cookie_closes_4401(self) -> None:
        with patch.object(chat_routes.session_auth, "load_session", return_value=None):
            self.assertEqual(_close_code(_client("stale-sid")), 4401)

    def test_missing_chat_closes_4404(self) -> None:
        rec = MagicMock()
        rec.tenant_id = "_default"
        with (
            patch.object(chat_routes.session_auth, "load_session", return_value=rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=None),
        ):
            self.assertEqual(_close_code(_client("valid-sid")), 4404)

    def test_source_never_closes_before_accept(self) -> None:
        src = (_REPO / "core/console/corvin_console/routes/chat.py").read_text(encoding="utf-8")
        body = src[src.index("async def chat_stream("):]
        body = body[:body.index("await websocket.send_json(")]
        first_close = body.index("await websocket.close(")
        self.assertLess(body.index("await websocket.accept()"), first_close)


if __name__ == "__main__":
    unittest.main()
