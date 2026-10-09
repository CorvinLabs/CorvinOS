"""Message lifecycle over HTTP — ``stages`` on the feed and ``/a2a/feed/task/{id}``.

Drives the real router + its session guard against a tenant-local store seeded with the
stores' own writers (``a2a_feed.record`` / ``a2a_feed.observe_stage``):

- the feed carries the furthest stage per task, not tied to the ``after`` cursor
  (a stage changes on a message the client already has)
- ``peer_id`` scopes the stage map to that peer
- the task endpoint returns the chain without message text, 404s for unknown ids,
  422s for oversized ids, 401 without a session, 403 for another tenant

Run: python3 -m pytest core/console/tests/test_a2a_task_lifecycle_routes.py -q
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

from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import a2a_feed as R  # noqa: E402
from test_agent_hub_feed_real_data import _fake_record  # noqa: E402


class TaskLifecycleRouteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="corvin-lifecycle-")
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

    def _client(self, tenant: str | None = "_default") -> TestClient:
        app = FastAPI()
        app.include_router(R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
                lambda: _fake_record(tenant))
        return TestClient(app)

    def _seed(self):
        out = R._feed.record(direction="out", kind="task", peer_id="peer-a", task_id="t-1",
                             text="SECRET TEXT", status="sent", tenant_id="_default")
        for st in ("delivered", "processing"):
            R._feed.observe_stage(peer_id="peer-a", task_id="t-1", stage=st, tenant_id="_default")
        R._feed.observe_stage(peer_id="peer-b", task_id="t-9", stage="completed", tenant_id="_default")
        return out

    def test_feed_carries_the_furthest_stage_independent_of_the_cursor(self):
        out = self._seed()
        c = self._client()
        body = c.get("/v1/console/a2a/feed").json()
        self.assertEqual(body["stages"]["t-1"]["stage"], "processing")
        # nothing is newer than the message — the stage map must still ride along
        again = c.get(f"/v1/console/a2a/feed?after={out['seq']}").json()
        self.assertEqual(again["messages"], [])
        self.assertEqual(again["stages"]["t-1"]["stage"], "processing")

    def test_stage_map_is_scoped_to_the_requested_peer(self):
        self._seed()
        body = self._client().get("/v1/console/a2a/feed?peer_id=peer-a").json()
        self.assertEqual(set(body["stages"]), {"t-1"})

    def test_empty_store_has_an_empty_stage_map_not_sample_data(self):
        self.assertEqual(self._client().get("/v1/console/a2a/feed").json()["stages"], {})

    def test_task_endpoint_returns_the_chain_without_message_text(self):
        self._seed()
        r = self._client().get("/v1/console/a2a/feed/task/t-1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["stage"]["stage"], "processing")
        self.assertEqual([t["stage"] for t in body["timeline"]], ["delivered", "processing"])
        self.assertEqual(body["messages"][0]["status"], "sent")
        self.assertNotIn("SECRET TEXT", r.text)

    def test_task_endpoint_guards(self):
        self._seed()
        self.assertEqual(self._client(tenant=None).get("/v1/console/a2a/feed/task/t-1").status_code, 401)
        self.assertEqual(self._client(tenant="tenant_b").get("/v1/console/a2a/feed/task/t-1").status_code, 403)
        self.assertEqual(self._client().get("/v1/console/a2a/feed/task/nope").status_code, 404)
        self.assertEqual(self._client().get("/v1/console/a2a/feed/task/" + "x" * 65).status_code, 422)


    def test_peers_carry_the_capacity_the_peer_reported_only_while_online(self):
        """ADR-2242 §8: "daily limit reached" is a fact about NOW — shown while the peer is
        online, never from a peer we can no longer see, and absent for an older peer."""
        import json
        import time
        now = time.time()
        home = Path(os.environ["CORVIN_HOME"])

        def put(pid, **cfg):
            for kind, key in (("origins", "origin_id"), ("endpoints", "endpoint_id")):
                (home / kind / f"{pid}.json").write_text(json.dumps({key: pid, "enabled": True, **cfg}))

        put("full-peer", state="ACTIVE", _last_check_at=now - 20, _last_ok_at=now - 20,
            _peer_task_capacity="limit_reached")
        put("gone-peer", state="UNREACHABLE", _last_check_at=now - 20, _last_ok_at=now - 650_000,
            _peer_task_capacity="limit_reached")
        put("old-peer", state="ACTIVE", _last_check_at=now - 20, _last_ok_at=now - 20)
        put("bad-peer", state="ACTIVE", _last_check_at=now - 20, _last_ok_at=now - 20,
            _peer_task_capacity="<script>")
        peers = {p["peer_id"]: p for p in self._client().get("/v1/console/a2a/feed").json()["peers"]}
        self.assertEqual(peers["full-peer"]["task_capacity"], "limit_reached")
        self.assertIsNone(peers["gone-peer"]["task_capacity"])
        self.assertIsNone(peers["old-peer"]["task_capacity"])
        self.assertIsNone(peers["bad-peer"]["task_capacity"], "an unknown value is never passed on")


    def test_a_removed_peer_row_also_carries_task_capacity(self):
        """Found by the live watch (2026-10-09): `include_former=true` — how the UI asks — appended rows for
        connections that no longer exist, built apart from presence(), without the key."""
        R._feed.record(direction="out", kind="task", peer_id="vanished", task_id="t-old",
                       text="x", status="sent", tenant_id="_default")
        body = self._client().get("/v1/console/a2a/feed?include_former=true").json()
        rows = {p["peer_id"]: p for p in body["peers"]}
        self.assertEqual(rows["vanished"]["presence"], "removed")
        self.assertIn("task_capacity", rows["vanished"])
        self.assertIsNone(rows["vanished"]["task_capacity"])
        for p in body["peers"]:
            self.assertIn("task_capacity", p, p.get("peer_id"))


if __name__ == "__main__":
    unittest.main()
