"""Third round of the 2026-11-19 console review (R3-CON-12, -14).

Routers are driven over HTTP (TestClient); only the session dependency is
replaced.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_review_2026_11_19_console_backend import (  # noqa: E402
    _chain_under,
    _client,
    _route_path,
    _write_chain,
)
from test_review_r2_console_backend import _force_small_window  # noqa: E402


# ───────────────────── R3-CON-12: phase3 tenant filter ──────────────────────

def _decision(ts, **extra):
    rec = {"event_type": "routing_decision_made", "ts": ts,
           "details": {"confidence": 0.9, "latency_ms": 5, "complexity_level": "simple",
                       "model_routed": "m"}}
    for key, value in extra.items():
        if key == "details_tenant":
            rec["details"]["tenant_id"] = value
        else:
            rec[key] = value
    return rec


def test_phase3_drops_records_naming_another_tenant_in_details(tmp_path, monkeypatch):
    """Chain records carry the tenant in ``details.tenant_id``; only the top
    level was read, so another tenant's record in this file was counted."""
    from corvin_console.routes import phase3_monitor as P
    monkeypatch.setattr(P, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
    now = time.time()
    _write_chain(_chain_under(tmp_path, "acme"), [
        _decision(now - 10),                                   # names nobody: this file's tenant
        _decision(now - 9, details_tenant="acme"),             # chain shape, own tenant
        _decision(now - 8, tenant_id="acme"),                  # flat shape, own tenant
        _decision(now - 7, details_tenant="other"),            # chain shape, FOREIGN  (was counted)
        _decision(now - 6, tenant_id="other"),                 # flat shape, foreign
        _decision(now - 5, tenant_id="acme", details_tenant="other"),   # disagreeing: dropped
        {"event_type": "routing_decision_error", "ts": now - 4, "details": {"tenant_id": "other"}},
        {"event_type": "routing_decision_error", "ts": now - 3, "details": {"tenant_id": "acme"}},
    ])
    body = _client(P.router, tenant="acme").get(_route_path(P.router, "get_phase3_performance")).json()
    assert body["metrics"]["decisions_made"] == 3, body["metrics"]
    metrics = P._read_audit_trail_metrics("acme", 24)
    assert metrics["decisions_made"] == 3 and metrics["error_count"] == 1


@pytest.mark.parametrize("event,tenant,expected", [
    ({"details": {"tenant_id": "b"}}, "a", True),
    ({"tenant_id": "b"}, "a", True),
    ({"tenant_id": "a", "details": {"tenant_id": "a"}}, "a", False),
    ({}, "a", False),
    ({"details": "not a dict", "tenant_id": ""}, "a", False),
    ({"details": {"tenant_id": 7}}, "a", False),
])
def test_names_other_tenant(event, tenant, expected):
    from corvin_console.routes import phase3_monitor as P
    assert P._names_other_tenant(event, tenant) is expected


# ───────────────────── R3-CON-14: panel ids, graph scan ─────────────────────

@pytest.mark.parametrize("bad", ["abc\n", "abc\n\n", "\nabc", "abc ", "ABC", "", "-abc", "a" * 50, "a/b", "a.b"])
def test_panel_id_must_match_completely(bad):
    from fastapi import HTTPException
    from corvin_console.routes import panels
    with pytest.raises(HTTPException):
        panels._validate_id(bad)                         # "abc\n" used to pass ($ before \n)
    with pytest.raises(ValueError):
        panels.store_panel("_default", bad, "t", "<p>x</p>")


def test_panel_id_positive_control():
    from corvin_console.routes import panels
    for good in ("a", "abc", "a-b-0", "0x", "a" * 49):
        assert panels._validate_id(good) == good


def _graph_chain(tmp_path, monkeypatch, n=40):
    from corvin_console.routes.vibe import audit_graph as G
    chain = _chain_under(tmp_path, "_default")
    monkeypatch.setattr(G, "_find_audit_chain", lambda tid: chain if chain.exists() else None)
    _write_chain(chain, [
        {"event_type": "boot.ok", "ts": 100.0 + i, "hash": f"{i:064d}",
         "prev_hash": f"{i - 1:064d}", "details": {}}
        for i in range(n)
    ])
    return G, _client(G.router), _route_path(G.router, "get_audit_graph")


def test_graph_time_window_is_applied_before_the_limit(tmp_path, monkeypatch):
    """limit=5 took the newest five records (ts 135..139) and filtered AFTER:
    a window of ts 100..110 came back empty with ``truncated: false``."""
    G, c, path = _graph_chain(tmp_path, monkeypatch)
    body = c.get(path, params={"limit": 5, "until": 110.0}).json()
    assert [n["ts"] for n in body["nodes"]] == [106.0, 107.0, 108.0, 109.0, 110.0]
    assert body["total_events"] == 5
    assert body["scan"]["limit_reached"] is True and body["scan"]["truncated"] is False

    body = c.get(path, params={"limit": 50, "since": 130.0, "until": 134.0}).json()
    assert [n["ts"] for n in body["nodes"]] == [130.0, 131.0, 132.0, 133.0, 134.0]
    assert body["scan"]["limit_reached"] is False and body["scan"]["truncated"] is False


def test_graph_window_outside_the_scanned_tail_is_marked_partial(tmp_path, monkeypatch):
    G, c, path = _graph_chain(tmp_path, monkeypatch)
    _force_small_window(monkeypatch, G, "iter_chain_tail", "scan_window")
    # The window lies in the part of the chain the tail scan does not reach.
    body = c.get(path, params={"limit": 5, "until": 105.0}).json()
    assert body["nodes"] == [] and body["total_events"] == 0
    assert body["scan"]["truncated"] is True, "an empty answer from a partial scan is not complete"
    assert body["scan"]["limit_reached"] is False
    # Without a window the newest record is a complete answer to limit=1.
    newest = c.get(path, params={"limit": 1}).json()
    assert newest["scan"]["truncated"] is False and newest["scan"]["limit_reached"] is True


def test_graph_without_a_chain_reports_the_same_scan_keys(tmp_path, monkeypatch):
    from corvin_console.routes.vibe import audit_graph as G
    monkeypatch.setattr(G, "_find_audit_chain", lambda tid: None)
    assert G._graph_scan("_default", 0, 10) == {
        "truncated": False, "limit_reached": False, "scanned_bytes": 0,
        "total_bytes": 0, "covers_since": None}


# ───────────────────── R3-CON-6: panel manifest (data declaration) ──────────

_PANEL_HTML = (
    '<!doctype html><html><head><title>Recent sessions</title>'
    '<meta name="corvin-panel-data" content="/sessions /vibe-engineering/traces, /sessions">'
    '</head><body><h1>Recent sessions</h1></body></html>'
)


def test_extract_data_paths_is_shape_only_and_bounded():
    from corvin_console.routes import panels
    assert panels.extract_data_paths(_PANEL_HTML) == ["/sessions", "/vibe-engineering/traces"]
    assert panels.extract_data_paths("<META content='/a/b' NAME='corvin-panel-data'>") == ["/a/b"]
    hostile = ('<meta name="corvin-panel-data" content="https://evil.example/x //evil /a/../b '
               '/a?b=1 /a/ /a%2fb /a.b a /ok">')
    assert panels.extract_data_paths(hostile) == ["/ok"]
    assert panels.extract_data_paths('<meta name="viewport" content="/sessions">') == []
    assert panels.extract_data_paths("") == [] and panels.extract_data_paths(None) == []
    many = '<meta name="corvin-panel-data" content="' + " ".join(f"/p{i}" for i in range(40)) + '">'
    assert len(panels.extract_data_paths(many)) == panels.MAX_DATA_PATHS


def test_manifest_route_serves_the_declaration_stored_at_install(tmp_path, monkeypatch):
    from corvin_console.routes import panels
    monkeypatch.setattr(panels, "_panels_dir", lambda tid: tmp_path / tid / "console_panels")
    (tmp_path / "acme" / "console_panels").mkdir(parents=True)
    meta = panels.store_panel("acme", "recent-sessions", "Recent sessions", _PANEL_HTML)
    assert meta["data_paths"] == ["/sessions", "/vibe-engineering/traces"]

    c = _client(panels.router, tenant="acme")
    body = c.get("/panels/recent-sessions/manifest").json()
    assert body == {"id": "recent-sessions", "title": "Recent sessions",
                    "data_paths": ["/sessions", "/vibe-engineering/traces"]}
    # The list the shell reads carries it too.
    listed = c.get("/panels").json()["panels"]
    assert listed[0]["data_paths"] == ["/sessions", "/vibe-engineering/traces"]
    # Another tenant has no such panel.
    (tmp_path / "other" / "console_panels").mkdir(parents=True)
    assert _client(panels.router, tenant="other").get("/panels/recent-sessions/manifest").status_code == 404
    assert c.get("/panels/nope/manifest").status_code == 404
    assert c.get("/panels/BAD ID/manifest").status_code == 400


def test_manifest_of_a_panel_installed_before_the_declaration_existed(tmp_path, monkeypatch):
    import json as _json
    from corvin_console.routes import panels
    monkeypatch.setattr(panels, "_panels_dir", lambda tid: tmp_path / tid / "console_panels")
    pdir = tmp_path / "acme" / "console_panels" / "old"
    pdir.mkdir(parents=True)
    (pdir / "index.html").write_text(_PANEL_HTML, encoding="utf-8")
    (pdir / "meta.json").write_text(_json.dumps({"id": "old", "title": "Old"}), encoding="utf-8")
    before = (pdir / "meta.json").read_bytes()
    c = _client(panels.router, tenant="acme")
    assert c.get("/panels/old/manifest").json()["data_paths"] == ["/sessions", "/vibe-engineering/traces"]
    assert (pdir / "meta.json").read_bytes() == before, "a read writes nothing"
    # A hand-edited meta.json cannot smuggle a malformed path through.
    (pdir / "meta.json").write_text(_json.dumps(
        {"id": "old", "title": 7, "data_paths": ["/sessions", "https://evil", 3, "/a/../b"]}), encoding="utf-8")
    assert c.get("/panels/old/manifest").json() == {"id": "old", "title": "Panel", "data_paths": ["/sessions"]}


def test_the_served_panel_document_keeps_its_sandbox_header(tmp_path, monkeypatch):
    """The data channel does not widen the sandbox: no allow-same-origin anywhere."""
    from corvin_console.routes import panels
    monkeypatch.setattr(panels, "_panels_dir", lambda tid: tmp_path / tid / "console_panels")
    (tmp_path / "acme" / "console_panels").mkdir(parents=True)
    panels.store_panel("acme", "p", "P", _PANEL_HTML)
    r = _client(panels.router, tenant="acme").get("/panels/p/index.html")
    assert r.headers["content-security-policy"] == "sandbox allow-scripts"
    web = Path(panels.__file__).resolve().parents[1] / "web-next" / "src" / "panels" / "PanelHost.tsx"
    assert 'export const PANEL_SANDBOX = "allow-scripts";' in web.read_text(encoding="utf-8")
