"""P-S1a — memory routes never touch the developer's ``~/.claude`` (PLAN §4 S-1a).

Red on main (3029088f8): ``core/console/corvin_console/routes/memory.py:33-61`` resolves
``~/.claude/projects/<slug>/memory`` for every session (``_MEMORY_DIR`` ``:64``), lists and
reads it (``GET /memory`` ``:116-117``, ``GET /memory/{name}``), writes it (``PUT`` ``:197``)
and deletes from it (``DELETE`` ``:281``). The sandbox HOME stands in for the real one; the
dev-memory slug is computed here, not imported, so the fix can delete the module's helpers.

Green after the planned fix (remove every ``~/.claude`` access; developer view dropped per
Q7; F-26 tenant view): no route returns the marker, PUT/DELETE leave the dir untouched.
"""
from __future__ import annotations

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402
from .conftest import MARKER_A, MEMORY_DELETE_TARGET, MEMORY_FILE_A  # noqa: E402


def _dev_memory(sandbox):
    return harness.tree_hashes(sandbox.home / ".claude" / "projects", "*/memory/*")


def test_P_S1a_no_memory_route_returns_the_dev_memory_marker(server, sandbox, planted, routes,
                                                             sess_a, sess_b):
    # Controls that survive the fix: GET /memory is either gone (404/405/410 — the fix may
    # remove the route until F-26 replaces the view) or served for the caller's own tenant;
    # and the planted file sits exactly where the pre-fix code reads it.
    r = harness.fetch(server, sess_b, "/memory")
    assert r.status_code in (404, 405, 410) or (
        r.status_code == 200 and r.json().get("tenant_id") == harness.TENANT_B), \
        f"GET /memory neither absent nor tenant-scoped: {r.status_code} {r.text[:300]}"
    assert (planted["memory_dir"] / MEMORY_FILE_A).is_file()

    hits = []
    for route in harness.get_routes(routes):
        if not route["path"].startswith("/memory"):
            continue
        path = route["path"]
        for p in route["params"]:
            path = path.replace("{" + p + "}", MEMORY_FILE_A)
        for sess in (sess_a, sess_b):
            resp = harness.fetch(server, sess, path)
            if MARKER_A in resp.text:
                hits.append(f"{sess.tenant} {resp.status_code} {path}")
    assert not hits, f"dev-memory marker served by memory routes: {hits}"


def test_P_S1a_put_writes_nothing_under_dev_memory(server, sandbox, planted, sess_b):
    before = _dev_memory(sandbox)
    r = harness.fetch(server, sess_b, "/memory/user_sbx_put_probe.md", method="PUT",
                      json_body={"body": "probe"})
    after = _dev_memory(sandbox)
    assert r.status_code in (404, 405), f"PUT /memory still routed: {r.status_code} {r.text[:200]}"
    assert after == before, f"PUT /memory changed the dev-memory dir (status {r.status_code}): " \
                            f"{sorted(set(after) ^ set(before))}"


def test_P_S1a_delete_removes_nothing_under_dev_memory(server, sandbox, planted, sess_b):
    before = _dev_memory(sandbox)
    assert any(k.endswith(MEMORY_DELETE_TARGET) for k in before)
    r = harness.fetch(server, sess_b, f"/memory/{MEMORY_DELETE_TARGET}", method="DELETE",
                      json_body={})
    after = _dev_memory(sandbox)
    assert r.status_code in (404, 405), f"DELETE /memory still routed: {r.status_code} {r.text[:200]}"
    assert after == before, f"DELETE /memory removed a dev-memory file (status {r.status_code}): " \
                            f"{sorted(set(after) ^ set(before))}"


def test_P_S1a_get_memory_is_an_honest_empty_state_without_a_path(server, sess_a):
    r = harness.fetch(server, sess_a, "/memory")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["tenant_id"] == harness.TENANT_A
    assert body["available"] is False and body["files"] == [] and body["count"] == 0
    assert "memory_dir" not in body and ".claude" not in r.text, r.text[:300]
