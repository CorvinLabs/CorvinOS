"""E2E: a forbidden status transition on abort is a 409 over HTTP, never a 500.

Real boundaries, nothing in the middle is mocked:
  * the REAL tasks router (``routes/tasks.py``) mounted on a FastAPI app and driven
    through ``TestClient`` — ``POST /v1/console/tasks`` creates the task and
    ``POST /v1/console/tasks/{id}/abort`` cancels it;
  * the REAL ``TaskQueue`` guard (``corvin_core.task_states``) refuses the move.

Only the auth dependencies are overridden (no session in a test).

One injected condition, stated: ``abort_task_handler`` returns early for a terminal
task, so a forbidden transition is only reachable through a stale read. The abort
handler's tenant-scoped read is made to report RUNNING while the log already says
COMPLETED. The guard and the 409 mapping are the real code.

Sandbox: CORVIN_HOME / XDG_CONFIG_HOME / HOME / CORVIN_AUDIT_ANCHOR_KEY (absolute)
in a temp dir; FORGE_ROOT unset.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in ("core/console", "corvin_operator/bridges/shared", "corvin_operator/forge"):
    _path = str(_REPO / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import deps  # noqa: E402
from corvin_console.routes import tasks as tasks_route  # noqa: E402
from corvin_console.routes import tasks_impl  # noqa: E402
from corvin_console.task_queue import TaskQueue  # noqa: E402
from corvin_core.task_states import InvalidTransitionError, TaskState  # noqa: E402

_TENANT = "_default"


class _Rec:
    tenant_id = _TENANT
    sid_fingerprint = "test-fp"
    csrf_token = "t"


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    (home / "tenants" / _TENANT / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", _TENANT)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)

    app = FastAPI()
    app.include_router(tasks_route.router, prefix="/v1/console")
    app.dependency_overrides[deps.require_csrf] = lambda: _Rec()
    app.dependency_overrides[deps.require_session] = lambda: _Rec()
    return TestClient(app)


def _queue() -> TaskQueue:
    return TaskQueue(tasks_impl._forge_paths.tenant_global_dir(_TENANT))


def test_corvin_core_task_states_import_resolves_to_the_guard_class():
    # The 409 mapping in tasks_impl catches this exact class; a second copy of the
    # module would make the except clause silently miss and turn the 409 into a 500.
    assert tasks_impl.InvalidTransitionError is InvalidTransitionError
    assert issubclass(InvalidTransitionError, Exception)
    assert TaskState.CANCELLED.value == "cancelled"


def test_forbidden_transition_on_abort_is_409_over_http_not_500(client, monkeypatch):
    resp = client.post("/v1/console/tasks", json={
        "chat_key": "web:abort-409", "instruction": "already finished",
    })
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    queue = _queue()
    entry = queue.dequeue(_TENANT)
    assert entry is not None and entry.task_id == task_id
    queue.update_status(task_id, TaskState.COMPLETED, exit_code=0)

    real_get = TaskQueue.get_task
    stale = {"pending": True}

    def stale_read_once(self, tid, tenant_id=None):
        now = real_get(self, tid, tenant_id=tenant_id)
        # Only the abort handler's tenant-scoped read is stale; the guard's own read
        # inside update_status sees the real COMPLETED state.
        if tenant_id is not None and stale["pending"] and now is not None:
            stale["pending"] = False
            return dataclasses.replace(now, status=TaskState.RUNNING)
        return now

    monkeypatch.setattr(TaskQueue, "get_task", stale_read_once)
    resp = client.post(f"/v1/console/tasks/{task_id}/abort")
    assert resp.status_code == 409, resp.text
    assert resp.status_code != 500
    assert "completed -> cancelled" in resp.json()["detail"]

    monkeypatch.setattr(TaskQueue, "get_task", real_get)
    assert client.get(f"/v1/console/tasks/{task_id}").json()["status"] == "completed"
