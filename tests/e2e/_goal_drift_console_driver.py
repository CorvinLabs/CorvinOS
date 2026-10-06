"""Subprocess driver for test_goal_drift_console_e2e.py.

Run as a disposable child process and ``os._exit(0)`` right after writing
its result summary. The console app's ASGI lifespan SHUTDOWN hangs
indefinitely in this environment (reproduced on the pre-existing,
unmodified ``test_session_ledger_console_e2e.py`` too — not a regression
from this change). Running the real turn loop in a child process that is
simply reaped, rather than cleanly exited, sidesteps the hang without
touching production code or the parent pytest process.

argv: <sandbox_home> <port> <flags_json> <result_json_path>
"""
from __future__ import annotations

import json
import os
import sys

GOAL_TEXT = "Goal for this chat: migrate the billing export to Parquet"
DRIFT_TEXT = "totally unrelated small talk about the weather today"


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
    r = c.post("/v1/console/chat/sessions", json={"title": "goal-drift-e2e"},
               headers={"x-csrf-token": csrf})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["sid"]
    for text in (GOAL_TEXT, DRIFT_TEXT, DRIFT_TEXT, DRIFT_TEXT):
        ws = c.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream")
        ws.__enter__()
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user", "text": text})
        for _ in range(400):
            if ws.receive_json().get("type") == "done":
                break

    alerts = []
    from pathlib import Path  # noqa: PLC0415
    for p in Path(home_dir).rglob("forge/audit.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("event_type") == "goal_drift.alert_raised":
                alerts.append(rec)
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump({"alerts": alerts}, fh)
    os._exit(0)  # noqa: PLC0415 — skip ASGI lifespan shutdown (see module docstring)


if __name__ == "__main__":
    main()
