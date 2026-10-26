"""T-0252 / ADR-2300 step 2 over HTTP — the console side of the sender outbox.

Drives the real router and its session guard against a tenant-local store seeded with the stores' own
writers (``a2a_outbox.enqueue``, ``a2a_feed.record``):

- ``GET /a2a/feed`` reports the waiting messages (task id, deadline, attempts) and NEVER their text, scoped
  by ``peer_id``;
- ``POST /a2a/feed/outbox/{task_id}/cancel`` withdraws a waiting message, ends its feed row as an error and
  audits it; 404 for anything that is not waiting; 401 without a session; another tenant sees nothing;
- ``_send_in_background`` (what ``POST /a2a/feed/send`` queues) opts plain text into the outbox and
  attachments out of it.

Run: python3 -m pytest core/console/tests/test_a2a_outbox_routes.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import a2a_outbox  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import a2a_feed as R  # noqa: E402
from test_agent_hub_feed_real_data import _fake_record  # noqa: E402


class OutboxRouteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="corvin-outbox-routes-")
        self.addCleanup(self.td.cleanup)
        home = Path(self.td.name)
        (home / "origins").mkdir()
        (home / "endpoints").mkdir()
        p = patch.dict(os.environ, {
            "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
            "REMOTE_ORIGINS_DIR": str(home / "origins"), "REMOTE_ENDPOINTS_DIR": str(home / "endpoints"),
        })
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        a2a_outbox._reset_for_tests()

    def _client(self, tenant: str | None = "_default") -> TestClient:
        app = FastAPI()
        app.include_router(R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
                lambda: _fake_record(tenant))
        return TestClient(app)

    def _keep(self, task="t-1", peer="peer-a", text="PRIVATE WAITING TEXT", tenant="_default"):
        R._feed.record(direction="out", kind="task", peer_id=peer, task_id=task, text=text,
                       status="queued", tenant_id=tenant)
        return a2a_outbox.enqueue(peer, task, text, deadline_s=3600, maybe_delivered=True, tenant_id=tenant)

    def test_the_feed_reports_waiting_messages_without_their_text(self):
        self._keep("t-1", "peer-a")
        self._keep("t-2", "peer-b")
        body = self._client().get("/v1/console/a2a/feed").json()
        self.assertEqual(set(body["outbox"]), {"t-1", "t-2"})
        self.assertEqual(set(body["outbox"]["t-1"]), {"deadline_at", "attempts"})
        self.assertNotIn("PRIVATE WAITING TEXT", str(body["outbox"]))

    def test_the_outbox_map_is_scoped_to_the_requested_peer(self):
        self._keep("t-1", "peer-a")
        self._keep("t-2", "peer-b")
        body = self._client().get("/v1/console/a2a/feed?peer_id=peer-a").json()
        self.assertEqual(set(body["outbox"]), {"t-1"})

    def test_an_empty_outbox_is_an_empty_map_not_sample_data(self):
        self.assertEqual(self._client().get("/v1/console/a2a/feed").json()["outbox"], {})

    def test_cancel_withdraws_the_message_ends_its_row_and_deletes_the_text(self):
        self._keep("t-1")
        r = self._client().post("/v1/console/a2a/feed/outbox/t-1/cancel")
        self.assertEqual((r.status_code, r.json()["cancelled"]), (200, True))
        self.assertEqual(self._client().get("/v1/console/a2a/feed").json()["outbox"], {})
        entry = a2a_outbox.get("t-1")
        self.assertEqual((entry["state"], entry["text"]), ("cancelled", ""))
        rows = [m for m in R._feed.read(limit=50, tenant_id="_default")
                if m["kind"] == "response" and m["task_id"] == "t-1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "error")
        self.assertIn("Cancelled", rows[0]["error"])

    def test_cancel_is_refused_for_anything_that_is_not_waiting(self):
        c = self._client()
        self.assertEqual(c.post("/v1/console/a2a/feed/outbox/never-existed/cancel").status_code, 404)
        self._keep("t-1")
        self.assertEqual(c.post("/v1/console/a2a/feed/outbox/t-1/cancel").status_code, 200)
        self.assertEqual(c.post("/v1/console/a2a/feed/outbox/t-1/cancel").status_code, 404, "twice")
        self._keep("t-2")
        a2a_outbox.close("t-2", "sent")
        self.assertEqual(c.post("/v1/console/a2a/feed/outbox/t-2/cancel").status_code, 404, "already sent")

    def test_cancel_guards_session_and_tenant(self):
        self._keep("t-1")
        self.assertEqual(self._client(tenant=None).post("/v1/console/a2a/feed/outbox/t-1/cancel").status_code, 401)
        # another tenant has its own outbox: it cannot see, let alone cancel, this one
        other = self._client(tenant="tenant_b")
        self.assertIn(other.post("/v1/console/a2a/feed/outbox/t-1/cancel").status_code, (403, 404))
        self.assertEqual(a2a_outbox.get("t-1")["state"], "waiting")
        self.assertEqual(self._client().get("/v1/console/a2a/feed").json()["outbox"].keys(), {"t-1"})


class SendInBackgroundOptsInTests(unittest.TestCase):
    """The reachability link: the route's background job is what asks the sender to use the outbox."""

    def _capture(self, text="hello", atts=None):
        seen: dict = {}

        class FakeSender:
            def send(self, peer_id, text, **kw):
                seen.update(kw, peer_id=peer_id, text=text)

        import remote_trigger_sender as rts
        with patch.object(rts, "RemoteTriggerSender", FakeSender):
            R._send_in_background("peer-a", text, atts or [], None, "task-9")
        return seen

    def test_plain_text_asks_for_the_outbox_with_the_default_deadline(self):
        seen = self._capture()
        self.assertEqual(seen["outbox_deadline_s"], a2a_outbox.DEFAULT_DEADLINE_S)
        self.assertEqual((seen["task_id"], seen["feed_task_recorded"]), ("task-9", True))

    def test_a_message_with_attachments_never_does(self):
        seen = self._capture(atts=[{"name": "a.txt", "mime": "text/plain", "sha256": "0" * 64,
                                    "content_b64": "aGk="}])
        self.assertIsNone(seen["outbox_deadline_s"])


if __name__ == "__main__":
    unittest.main()
