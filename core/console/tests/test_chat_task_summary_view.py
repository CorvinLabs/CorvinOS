"""``GET /chat/sessions/{sid}/tasks?view=summary`` — the chat sidebar indicator's feed.

Driven over real HTTP against the real route and a real on-disk TaskManager;
only session auth and the session lookup are substituted.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
for p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    sys.path.insert(0, str(_REPO / p))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import corvin_console.chat_runtime as chat_runtime  # noqa: E402
import corvin_console.routes.chat as chat_routes  # noqa: E402
from corvin_core.task_manager import TaskManager  # noqa: E402


class TaskSummaryViewTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sess = chat_runtime.WebChatSession(
            sid="s1", tenant_id="_default", created_at=0.0, last_active_at=0.0,
            workdir=Path(tmp.name) / "web_s1",
        )
        self.sess.workdir.mkdir(parents=True)
        self.tm = TaskManager(self.sess.workdir / "tasks")
        rec = MagicMock(tenant_id="_default", sid_fingerprint="fp")
        for p in (
            patch.object(chat_routes.session_auth, "load_session", return_value=rec),
            patch.object(chat_routes.chat_runtime, "get_session", return_value=self.sess),
        ):
            p.start()
            self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(chat_routes.router, prefix="/v1/console")
        self.client = TestClient(app)
        self.client.cookies.set("corvin_console_sid", "valid-sid")

    def _new(self, instruction: str) -> str:
        return self.tm.create_task(
            chat_key=self.sess.chat_key, instruction=instruction,
            persona="assistant", tenant_id="_default",
        )

    def _get(self, **params) -> dict:
        r = self.client.get("/v1/console/chat/sessions/s1/tasks", params={"view": "summary", **params})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_running_task_is_reported_newest_first_with_server_clock(self) -> None:
        done = self._new("first")
        self.tm.record_event(done, {"event": "task.started"})
        self.tm.record_event(done, {"event": "task.completed", "exit_code": 0})
        time.sleep(0.02)
        live = self._new("second")
        self.tm.record_event(live, {"event": "task.started"})

        before = time.time()
        body = self._get()
        self.assertLessEqual(before, body["now"])
        self.assertEqual([t["task_id"] for t in body["tasks"]], [live, done])
        head = body["tasks"][0]
        self.assertEqual(head["status"], "running")
        self.assertIsNotNone(head["started_at"])
        self.assertLess(head["created_at"], 1e12)  # epoch seconds
        self.assertIsNotNone(head["last_event_at"])
        self.assertEqual(body["tasks"][1]["status"], "completed")

    def test_full_instruction_never_leaves_the_server(self) -> None:
        self._new("x" * 5_000)
        t = self._get()["tasks"][0]
        self.assertNotIn("input", t)
        self.assertEqual(len(t["instruction_preview"]), 120)

    def test_limit_bounds_the_scan(self) -> None:
        for i in range(5):
            self._new(f"t{i}")
            time.sleep(0.01)
        body = self._get(limit=2)
        self.assertEqual([t["instruction_preview"] for t in body["tasks"]], ["t4", "t3"])

    def test_full_view_is_unchanged(self) -> None:
        self._new("keep the full shape")
        r = self.client.get("/v1/console/chat/sessions/s1/tasks")
        self.assertEqual(r.status_code, 200)
        t = r.json()["tasks"][0]
        self.assertEqual(t["input"]["instruction"], "keep the full shape")
        self.assertNotIn("now", r.json())


if __name__ == "__main__":
    unittest.main()
