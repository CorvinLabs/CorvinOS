"""Subprocess driver for test_context_bridge_console_e2e.py.

Same disposable-child-process pattern as _goal_drift_console_driver.py (see
that module's docstring for why: the console app's ASGI lifespan SHUTDOWN
hangs indefinitely in this sandbox). Unlike the goal-drift driver, /resume
is intercepted before any engine spawn or house-rules classification, so
this one does not need the generous timeout the goal-drift console E2E
needed — it never reaches that code path at all.

argv: <sandbox_home> <port> <flags_json> <result_json_path>
"""
from __future__ import annotations

import json
import os
import sys

TASK_ID = "T-CTXBRIDGE-E2E"


def main() -> None:
    home_dir, port_s, flags_json, result_path = sys.argv[1:5]
    port = int(port_s)
    flags = json.loads(flags_json)

    from corvin_core import feature_flags as ff  # noqa: PLC0415
    for flag_id, val in flags.items():
        ff.set_enabled(flag_id, val, "_default")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.console.corvin_console.app import router as console_router

    app = FastAPI()
    app.include_router(console_router, prefix="/v1/console")
    c = TestClient(app, client=("127.0.0.1", port))
    c.__enter__()
    r = c.get("/v1/console/auth/local-login", follow_redirects=False)
    assert r.status_code in (200, 302, 307), r.text
    csrf = c.get("/v1/console/auth/whoami").json()["csrf_token"]

    def _new_session() -> str:
        resp = c.post("/v1/console/chat/sessions", json={"title": "context-bridge-e2e"},
                      headers={"x-csrf-token": csrf})
        assert resp.status_code == 200, resp.text
        return resp.json()["session"]["sid"]

    replies: list[str] = []

    # Connection 1: /resume with nothing snapshotted yet, then disconnect —
    # the disconnect handler in routes/chat.py must fire the snapshot.
    sid1 = _new_session()
    with c.websocket_connect(f"/v1/console/chat/sessions/{sid1}/stream") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user", "text": f"/resume {TASK_ID}"})
        assert ws.receive_json()["type"] == "delta"
        replies.append("first-resume")
        assert ws.receive_json()["type"] == "done"
    # `with` exit here closes the client side; the server observes
    # WebSocketDisconnect and runs _maybe_snapshot_active_task().

    # Connection 2: fresh session, /resume the SAME task_id — must restore.
    sid2 = _new_session()
    restore_reply = None
    with c.websocket_connect(f"/v1/console/chat/sessions/{sid2}/stream") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user", "text": f"/resume {TASK_ID}"})
        ev = ws.receive_json()
        assert ev["type"] == "delta"
        restore_reply = ev["text"]
        assert ws.receive_json()["type"] == "done"

    created, restored = [], []
    from pathlib import Path  # noqa: PLC0415
    for p in Path(home_dir).rglob("forge/audit.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("event_type") == "context_bridge.snapshot_created":
                created.append(rec)
            elif rec.get("event_type") == "context_bridge.restored":
                restored.append(rec)

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump({
            "restore_reply": restore_reply,
            "snapshot_created": created,
            "restored": restored,
        }, fh)
    os._exit(0)  # noqa: PLC0415 — skip ASGI lifespan shutdown (see module docstring)


if __name__ == "__main__":
    main()
