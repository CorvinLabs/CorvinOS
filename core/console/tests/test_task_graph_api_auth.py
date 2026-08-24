"""E2E security regression tests for the Task Graph API (ADR-0400).

Adversarial review found ``routes/task_graph_api.py`` served every endpoint
with a hardcoded ``get_current_user`` stub (tenant_id="default"), no
``Depends(require_session)``, and a GLOBAL checkpoint dir — i.e. no session
auth and no tenant isolation. This drives the REAL FastAPI app through the
TestClient transport and proves, for EVERY endpoint:

  (a) an UNAUTHENTICATED request is rejected (401), not served;
  (b) an authenticated session for tenant B cannot read tenant A's graph
      (cross-tenant isolation — 404, not another tenant's data);
  (c) an authenticated same-tenant request still works (200).

The unauthenticated + cross-tenant assertions are parametrized over ALL FIVE
endpoints so removing ``require_session`` from ANY single one fails at least
one test (mutation-proof, not just the two originally covered).

A separate test proves the checkpoint layer's path-traversal guard is
INTRINSIC (``_safe_task_id`` inside ``get_task_graph`` / the snapshot handler)
rather than merely a side effect of the router's ``[^/]+`` path constraint.

CLAUDE.md multi-tenant axis (ADR-0007) + GDPR Art. 5/32.
"""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


# Every task-scoped endpoint, as (method, path_template, request_kwargs).
# ``{tid}`` is substituted with the target task_id. Each carries whatever
# required query/body params it needs to get PAST validation and reach the
# auth + tenant-scoping logic (so a 401/404 is about auth/isolation, not a 422).
_TASK_ENDPOINTS = [
    ("GET", "/v1/console/api/tasks/{tid}/graph", {}),
    ("GET", "/v1/console/api/tasks/{tid}/graph/query", {"params": {"type": "timeline"}}),
    ("GET", "/v1/console/api/tasks/{tid}/graph/snapshot", {}),
    ("POST", "/v1/console/api/tasks/{tid}/graph/export", {"params": {"format": "json"}}),
]
# The listing endpoint takes no task_id.
_LIST_ENDPOINT = ("GET", "/v1/console/api/tasks/graphs", {})


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@contextmanager
def _app(tmp_path: Path):
    """Self-contained console app; yields (client, session_auth, task_graph_api)."""
    home = tmp_path / "corvin_home"
    for tid in ("_default", "tenanta", "tenantb"):
        (home / "tenants" / tid / "global" / "auth").mkdir(parents=True, exist_ok=True)
        (home / "tenants" / tid / "global" / "console" / "sessions").mkdir(
            parents=True, exist_ok=True
        )

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = "_default"

    try:
        _reset_modules()
        from corvin_console import auth as session_auth
        from corvin_console.app import router
        from corvin_console.routes import task_graph_api
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        yield client, session_auth, task_graph_api
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def _seed_graph(task_graph_api, tenant_id: str, task_id: str) -> None:
    """Persist one checkpoint into ``tenant_id``'s scoped checkpoint dir."""
    manager = task_graph_api._manager_for_tenant(tenant_id)
    assert manager is not None, "CheckpointManager unavailable — cannot seed"
    cp = manager.create_checkpoint(
        task_id=task_id,
        session_id="sess-1",
        phase="build",
        trigger="manual",
        iteration_num=1,
        task_state={"task_id": task_id, "goal": "demo goal"},
        context_essentials={"kept": [], "dropped": [], "reduction_pct": 91},
        learning_state={"strategies_tried": []},
        open_subgoals=[],
        artifacts=[],
    )
    manager.save(cp)


def _call(client, method, path, kwargs):
    return client.request(method, path, **kwargs)


class TestTaskGraphApiAuth(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unauthenticated_is_rejected(self):
        """Every endpoint — task-scoped AND the listing — rejects a request
        with no session cookie (401). Removing ``require_session`` from any one
        endpoint makes its assertion here fail."""
        with _app(self.tmp_path) as (client, _auth, tga):
            _seed_graph(tga, "tenanta", "task-xyz")
            # No session cookie set.
            for method, tmpl, kw in _TASK_ENDPOINTS:
                path = tmpl.format(tid="task-xyz")
                r = _call(client, method, path, kw)
                self.assertEqual(r.status_code, 401, f"{method} {path} -> {r.text}")

            m, p, kw = _LIST_ENDPOINT
            r = _call(client, m, p, kw)
            self.assertEqual(r.status_code, 401, f"{m} {p} -> {r.text}")

    def test_cross_tenant_read_denied(self):
        """An authenticated tenant B session cannot read tenant A's graph via
        ANY task-scoped endpoint (404, isolated), and B's listing never reveals
        A's task."""
        with _app(self.tmp_path) as (client, session_auth, tga):
            _seed_graph(tga, "tenanta", "task-xyz")

            rec_b = session_auth.create_session(tenant_id="tenantb")
            client.cookies.set("corvin_console_sid", rec_b.sid)

            # Tenant B tries to read tenant A's graph on every task endpoint
            # -> not found (isolated), never tenant A's data.
            for method, tmpl, kw in _TASK_ENDPOINTS:
                path = tmpl.format(tid="task-xyz")
                r = _call(client, method, path, kw)
                self.assertEqual(r.status_code, 404, f"{method} {path} -> {r.text}")

            # And tenant B's task listing must not reveal tenant A's task.
            m, p, kw = _LIST_ENDPOINT
            r2 = _call(client, m, p, kw)
            self.assertEqual(r2.status_code, 200, r2.text)
            ids = [t["task_id"] for t in r2.json()["tasks"]]
            self.assertNotIn("task-xyz", ids)

    def test_same_tenant_read_works(self):
        """The owning tenant still gets 200 on every endpoint (no regression)."""
        with _app(self.tmp_path) as (client, session_auth, tga):
            _seed_graph(tga, "tenanta", "task-xyz")

            rec_a = session_auth.create_session(tenant_id="tenanta")
            client.cookies.set("corvin_console_sid", rec_a.sid)

            for method, tmpl, kw in _TASK_ENDPOINTS:
                path = tmpl.format(tid="task-xyz")
                r = _call(client, method, path, kw)
                self.assertEqual(r.status_code, 200, f"{method} {path} -> {r.text}")

            # Listing shows the owner's own task.
            m, p, kw = _LIST_ENDPOINT
            r2 = _call(client, m, p, kw)
            self.assertEqual(r2.status_code, 200, r2.text)
            ids = [t["task_id"] for t in r2.json()["tasks"]]
            self.assertIn("task-xyz", ids)

    def test_traversal_task_id_rejected_at_manager_layer(self):
        """Defense-in-depth: the checkpoint layer's path-traversal guard is
        INTRINSIC. Calling ``get_task_graph`` directly with a task_id that
        carries ``..``/separators must be rejected BEFORE any glob/read, so it
        cannot escape the tenant dir even absent the router's ``[^/]+``
        constraint. Proven by seeding a *real* graph in tenant B and showing a
        tenant-A manager + traversal task_id pointed at it raises rather than
        returning B's data."""
        from fastapi import HTTPException

        with _app(self.tmp_path) as (client, _auth, tga):
            # A real, readable graph in tenant B.
            _seed_graph(tga, "tenantb", "secret")

            manager_a = tga._manager_for_tenant("tenanta")
            # tenanta checkpoint dir is <home>/tenants/tenanta/vibe/checkpoints;
            # this relative path would glob into tenantb's dir if unguarded.
            traversal = "../../tenantb/vibe/checkpoints/secret"

            with self.assertRaises(HTTPException) as ctx:
                tga.get_task_graph(traversal, manager_a)
            self.assertEqual(ctx.exception.status_code, 400)

            # A range of hostile shapes are all rejected at the function layer.
            for bad in ("..", "a/b", "a\\b", "", "   ", "x\x00y", "../../etc/passwd"):
                with self.assertRaises(HTTPException) as ctx2:
                    tga.get_task_graph(bad, manager_a)
                self.assertEqual(ctx2.exception.status_code, 400, f"bad={bad!r}")

            # The helper itself is the single source of truth.
            with self.assertRaises(HTTPException):
                tga._safe_task_id("../evil")
            # A clean id passes straight through.
            self.assertEqual(tga._safe_task_id("task-xyz"), "task-xyz")


if __name__ == "__main__":
    unittest.main()
