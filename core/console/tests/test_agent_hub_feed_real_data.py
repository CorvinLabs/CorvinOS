"""Agent Hub live feed — real store records over HTTP, honest empty state.

The Agent Hub's default tab renders ``GET /v1/console/a2a/feed``. This drives
that route through the real FastAPI router and its router-level session guard
(no direct function calls) against a tenant-local content store in a tmp
``CORVIN_HOME``, seeded with the store's own writer (``a2a_feed.record``):

- no session            -> 401 (guard is live, route is mounted)
- empty store           -> ``messages == []`` (never sample data)
- seeded store          -> exactly the seeded records, oldest first
- other-tenant session  -> 403, not someone else's feed

Run: python3 -m pytest core/console/tests/test_agent_hub_feed_real_data.py -q
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import a2a_feed as R  # noqa: E402


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = 1_000_000.0 + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class AgentHubFeedRealDataTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="corvin-feed-")
        self.addCleanup(self.td.cleanup)
        home = Path(self.td.name)
        (home / "origins").mkdir()
        (home / "endpoints").mkdir()
        env = {
            "CORVIN_HOME": str(home),
            "CORVIN_TENANT_ID": "_default",
            "REMOTE_ORIGINS_DIR": str(home / "origins"),
            "REMOTE_ENDPOINTS_DIR": str(home / "endpoints"),
        }
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        self.home = home

    def _client(self, tenant: str | None = "_default") -> TestClient:
        app = FastAPI()
        app.include_router(R.router, prefix="/v1/console")
        if tenant is not None:
            app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
                lambda: _fake_record(tenant)
            )
        return TestClient(app)

    def test_no_session_is_401(self):
        self.assertEqual(self._client(tenant=None).get("/v1/console/a2a/feed").status_code, 401)

    def test_empty_store_returns_no_messages(self):
        r = self._client().get("/v1/console/a2a/feed")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["messages"], [])
        self.assertEqual(body["peers"], [])
        self.assertFalse(body["has_more"])

    def test_seeded_store_records_come_back(self):
        out = R._feed.record(direction="out", kind="task", peer_id="gpu-server",
                             task_id="t-1", text="render the chart", tenant_id="_default")
        back = R._feed.record(direction="in", kind="response", peer_id="gpu-server",
                              task_id="t-1", status="ok", text="done", tenant_id="_default")
        self.assertIsNotNone(out)
        self.assertIsNotNone(back)
        store = self.home / "tenants" / "_default" / "global" / "a2a_feed" / "messages.jsonl"
        self.assertTrue(store.is_file(), f"store not written under tmp CORVIN_HOME: {store}")

        body = self._client().get("/v1/console/a2a/feed").json()
        msgs = body["messages"]
        self.assertEqual([m["id"] for m in msgs], [out["id"], back["id"]])
        self.assertEqual([m["text"] for m in msgs], ["render the chart", "done"])
        self.assertEqual({m["task_id"] for m in msgs}, {"t-1"})
        self.assertEqual(body["last_seq"], back["seq"])

        # live cursor: nothing newer than the last seq
        again = self._client().get(f"/v1/console/a2a/feed?after={back['seq']}").json()
        self.assertEqual(again["messages"], [])

    def test_other_tenant_session_is_refused(self):
        R._feed.record(direction="out", kind="task", peer_id="p", task_id="t",
                       text="private", tenant_id="_default")
        r = self._client(tenant="tenant_b").get("/v1/console/a2a/feed")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("private", r.text)


if __name__ == "__main__":
    unittest.main()
