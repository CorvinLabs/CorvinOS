"""ADR-0701 G3 — the console forge routes, driven over HTTP through the real router.

The previous version of this file patched ``require_forge_capability`` itself
(the unit under test) and posted to a router mounted at the root, so every
request 404'd. These tests run the real dependency chain:

    TestClient → /v1/console/<route> → require_csrf → require_forge_capability
    → capability_api.require_capability → limits.CAPABILITIES

Free tier = a scratch CORVIN_HOME with no licence key. Member tier = the licence
resolver reporting ``member`` (see ``_license_console_sandbox``).

``POST/PUT /skills/manual`` is ALSO a G3 route per ADR-0701 but carries no
``require_forge_capability`` dependency today — see test_forge_create_real_flow.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import assert_402_forge, console, member_tier  # noqa: E402

# (route, a body that passes request validation for that route)
G3_ROUTES = [
    ("/v1/console/skill-creator/generate", {"user_request": "create a json validator skill"}),
    ("/v1/console/tools/some_tool/promote", {"to": "project"}),
    ("/v1/console/skills/some_skill/promote", {"to": "project"}),
    ("/v1/console/panels", {"id": "g3panel", "title": "G3", "html": "<p>x</p>"}),
]


@pytest.fixture
def con(tmp_path):
    with console(tmp_path) as c:
        yield c


@pytest.mark.parametrize("route,body", G3_ROUTES, ids=[r for r, _ in G3_ROUTES])
def test_refusal_matrix(con, route, body):
    # free tier (no licence key in the scratch home) → 402 from the real gate
    assert_402_forge(con.client.post(route, json=body, headers=con.h))
    # no session cookie → 401
    assert con.anonymous().post(route, json=body, headers=con.h).status_code == 401
    # session but no CSRF header → 403, before the licence is even consulted
    assert con.client.post(route, json=body).status_code == 403


def test_free_tier_panel_is_not_written(con):
    assert_402_forge(con.client.post(
        "/v1/console/panels", json={"id": "nopanel", "title": "t", "html": "<p/>"},
        headers=con.h))
    assert not list(con.home.rglob("nopanel"))
    assert con.client.get("/v1/console/panels").json()["panels"] == []


def test_member_can_create_a_panel(con):
    with member_tier():
        resp = con.client.post(
            "/v1/console/panels", json={"id": "mpanel", "title": "Member", "html": "<p>m</p>"},
            headers=con.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["route"] == "/app/mpanel"
    listed = con.client.get("/v1/console/panels").json()["panels"]
    assert [p["id"] for p in listed] == ["mpanel"]
    assert any(e.get("event_type") == "console.panel_created" for e in con.audit_events())


@pytest.mark.parametrize("route", [
    "/v1/console/tools/missing_tool/promote",
    "/v1/console/skills/missing_skill/promote",
])
def test_member_passes_the_gate_on_promote(con, route):
    """Past the gate the route's own logic answers (unknown artifact), never 402."""
    with member_tier():
        resp = con.client.post(route, json={"to": "project"}, headers=con.h)
    assert resp.status_code not in (401, 402, 403, 503), resp.text
    assert resp.status_code == 404, resp.text


def test_member_passes_the_gate_on_skill_creator(con):
    """A too-short request is rejected by the route's own validation (400/422),
    which only runs once the gate let the member through. No generation run is
    spawned for it."""
    with member_tier():
        resp = con.client.post(
            "/v1/console/skill-creator/generate", json={"user_request": "short"},
            headers=con.h)
    assert resp.status_code in (400, 422), resp.text
    assert resp.status_code != 402


def test_member_to_free_downgrade_closes_the_gate(con):
    with member_tier():
        ok = con.client.post(
            "/v1/console/panels", json={"id": "before", "title": "t", "html": "<p/>"},
            headers=con.h)
    assert ok.status_code == 200, ok.text
    assert_402_forge(con.client.post(
        "/v1/console/panels", json={"id": "after", "title": "t", "html": "<p/>"},
        headers=con.h))
    # The artifact made while licensed stays readable after the downgrade.
    assert [p["id"] for p in con.client.get("/v1/console/panels").json()["panels"]] == ["before"]
