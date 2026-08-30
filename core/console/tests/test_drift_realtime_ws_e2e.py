"""Drift-E2E — realtime WebSocket round-trip (task progress pub/sub).

A live turn's progress updates reach the Console over a WebSocket. If that
transport silently breaks (auth drift, fan-out drift, serialisation drift), a
long task looks dead even while it runs — a context-drift symptom the operator
feels directly.

This drives a REAL WebSocket through the real transport boundary:

  * a FastAPI ``TestClient`` opens ``/v1/console/tasks/progress`` (real ASGI
    WebSocket handshake through ``require_session`` auth),
  * an event is published into the SAME pub/sub singleton the handler subscribes
    to, executed on the app's own event loop via the session portal (so the
    asyncio.Queue wakeup is real, not a cross-thread hack),
  * the test asserts the exact payload arrives over the socket.

No component under test is mocked; the pub/sub fan-out, the queue, and the
``send_json`` framing all run for real.

Run: python3 -m pytest core/console/tests/test_drift_realtime_ws_e2e.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"

for _p in [str(_OPERATOR), str(_OPERATOR / "forge"), str(_CONSOLE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _sandbox(tmp_path: Path):
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from corvin_console import task_pubsub
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, rec.tenant_id, task_pubsub.get_pubsub()
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


class TestTaskProgressWebSocketE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _await_subscriber(self, pubsub, tenant_id, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pubsub.subscriber_count(tenant_id) >= 1:
                return True
            time.sleep(0.02)
        return False

    def test_published_event_reaches_the_socket(self):
        with _sandbox(Path(self._tmp)) as (client, tenant_id, pubsub):
            with client.websocket_connect("/v1/console/tasks/progress") as ws:
                # The handler registers its queue when the subscribe generator
                # starts. Wait for it so the publish cannot race ahead of it.
                self.assertTrue(
                    self._await_subscriber(pubsub, tenant_id),
                    "handler never registered a pub/sub subscriber")

                event = {"event": "progress", "pct": 42, "note": "halfway"}
                # Publish on the APP's event loop via the session portal, so the
                # asyncio.Queue.put_nowait + get() wakeup is real (not cross-loop).
                ws.portal.call(pubsub.publish, tenant_id, "task-123", event)

                got = ws.receive_json()
                self.assertEqual(got["task_id"], "task-123")
                self.assertEqual(got["event"], "progress")
                self.assertEqual(got["pct"], 42)
                self.assertEqual(got["note"], "halfway")

    def test_socket_requires_a_session(self):
        """Unauthenticated connect must be refused — the transport is not open
        to the world."""
        with _sandbox(Path(self._tmp)) as (client, _tenant, _pubsub):
            client.cookies.clear()
            from starlette.websockets import WebSocketDisconnect
            with self.assertRaises((WebSocketDisconnect, Exception)):
                with client.websocket_connect("/v1/console/tasks/progress") as ws:
                    ws.receive_json()


if __name__ == "__main__":
    unittest.main()
