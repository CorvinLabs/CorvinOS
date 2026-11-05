"""E2E tests for the panel API with its audit trail (ADR-0366 + ADR-0299).

Driven over HTTP through the REAL console router, mounted under ``/v1/console``
exactly like both shipped hosts do, with a persisted owner session, a scratch
``CORVIN_HOME`` and the real hash-chained audit writer (``_sandbox`` from
``test_admin_route.py``). The previous version built ``corvin_console.app.app``
(which mounts the router at the root, not under ``/v1/console``) and patched
``require_session`` on the route module, so every request was a 404 that never
reached the route.

``POST /panels`` is gated on ``forge.create`` (``require_forge_capability``): a
scratch home with no licence key is the Free tier, which is refused with 402.
The member tier is simulated by replacing the resolver the gate reads
(``capability_api.active_tier``) — the gate itself runs unmodified.

Verified:
1. Panel creation writes ``console.panel_created`` to the tenant chain.
2. Panel deletion writes ``console.panel_deleted``.
3. The panel HTML never reaches the audit record (metadata only, L16).
4. Free tier is refused (402) and nothing is stored.
5. A failing audit emitter does not block the panel (current route contract).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_admin_route import _audit_events, _sandbox  # noqa: E402

BASE = "/v1/console/panels"
TENANT = "_default"


@pytest.fixture
def con(tmp_path):
    import corvin_operator.license.capability_api as capability_api

    with _sandbox(tmp_path) as (client, csrf, home, _clients):
        with mock.patch.object(capability_api, "active_tier", lambda: "member"):
            yield client, {"X-CSRF-Token": csrf}, home


def _panel_events(home: Path, event_type: str) -> list[dict]:
    return [e for e in _audit_events(home, TENANT) if e.get("event_type") == event_type]


def _create(client, hdr, panel_id: str, html: str = "<h1>Test</h1>", title: str = "Test Panel"):
    return client.post(
        BASE,
        json={"id": panel_id, "title": title, "html": html, "nav_group": "test", "icon": "Test"},
        headers=hdr,
    )


def test_create_panel_emits_audit_event(con):
    client, hdr, home = con

    resp = _create(client, hdr, "test-panel")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["panel"]["id"] == "test-panel"
    assert body["route"] == "/app/test-panel"

    listed = client.get(BASE)
    assert listed.status_code == 200, listed.text
    assert [p["id"] for p in listed.json()["panels"]] == ["test-panel"]

    events = _panel_events(home, "console.panel_created")
    assert len(events) == 1, events
    details = events[0]["details"]
    assert details["panel_id"] == "test-panel"
    assert details["nav_group"] == "test"
    assert events[0]["details"]["tenant_id"] == TENANT


def test_delete_panel_emits_audit_event(con):
    client, hdr, home = con
    assert _create(client, hdr, "test-panel").status_code == 200

    resp = client.delete(f"{BASE}/test-panel", headers=hdr)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["deleted"] == "test-panel"
    assert client.get(BASE).json()["panels"] == []

    events = _panel_events(home, "console.panel_deleted")
    assert len(events) == 1, events
    assert events[0]["details"]["panel_id"] == "test-panel"
    assert events[0]["details"]["tenant_id"] == TENANT


def test_create_panel_with_html_not_in_audit(con):
    """Panel HTML is NOT included in the audit trail (L16 compliance)."""
    client, hdr, home = con
    html = "<h1>Secret HTML Content</h1><script>alert('xss')</script>"

    assert _create(client, hdr, "test-html-panel", html=html, title="Test HTML Panel").status_code == 200

    events = _panel_events(home, "console.panel_created")
    assert len(events) == 1, events
    blob = json.dumps(events[0])
    assert "Secret HTML" not in blob
    assert "<script>" not in blob
    assert "html" not in events[0]["details"]
    assert events[0]["details"]["panel_id"] == "test-html-panel"


def test_create_panel_is_refused_on_free_tier(tmp_path):
    with _sandbox(tmp_path) as (client, csrf, home, _clients):
        resp = _create(client, {"X-CSRF-Token": csrf}, "free-panel")
        assert resp.status_code == 402, resp.text
        assert client.get(BASE).json()["panels"] == []
        assert _panel_events(home, "console.panel_created") == []


def test_create_panel_requires_csrf(con):
    client, _hdr, home = con
    resp = client.post(BASE, json={"id": "no-csrf", "title": "x", "html": "<p/>"})
    assert resp.status_code == 403, resp.text
    assert _panel_events(home, "console.panel_created") == []


def test_create_panel_audit_resilient_to_failure(con):
    """If the audit emitter raises, panel creation still succeeds (route contract)."""
    client, hdr, _home = con
    with mock.patch("corvin_console.audit.panel_created", side_effect=RuntimeError("audit unavailable")):
        resp = _create(client, hdr, "test-panel-2", title="Test Panel 2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
