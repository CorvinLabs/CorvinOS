"""Refutation round of the 2026-11-19 console-backend review (R2-CON-1, -6, -11).

* R2-CON-1  every route that returns stored panel HTML sends
            ``Content-Security-Policy: sandbox allow-scripts`` - LLM-generated
            HTML opened top-level must not run same-origin with the session.
* R2-CON-6  a panel that reads only the tail of the audit chain SAYS so:
            ``chain_tail.scan_window`` has production callers, and each
            response carries ``scan`` (``truncated`` / ``scanned_bytes`` /
            ``total_bytes`` / ``covers_since``). The licence audit tail widens
            its scan for its rare events and reports coverage in headers.
* R2-CON-11 the vibe sub-routes resolve the chain through
            ``tenant_audit_chain``, never a hand-composed path.

Every chain file lives under a temp directory; the panel test runs the real
console router against a scratch ``CORVIN_HOME``.
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_review_2026_11_19_console_backend import (  # noqa: E402
    _ROUTES,
    _cel,
    _chain_under,
    _client,
    _route_path,
    _write_chain,
)

from corvin_console import chain_tail  # noqa: E402

SMALL = 600  # bytes: a tail window far smaller than the test chains


def _force_small_window(monkeypatch, module, *names: str) -> None:
    """Make ``module``'s chain readers behave as if the chain had outgrown the
    tail window, without writing a 32 MiB file."""
    for name in names:
        real = getattr(chain_tail, name)
        monkeypatch.setattr(
            module, name,
            lambda p, _real=real, **kw: _real(p, **{**kw, "max_scan_bytes": SMALL}),
        )


# ───────────────────────────── R2-CON-1: panel CSP ───────────────────────────


@pytest.fixture
def con(tmp_path):
    import corvin_operator.license.capability_api as capability_api
    from test_admin_route import _sandbox

    with _sandbox(tmp_path) as (client, csrf, home, _clients):
        with mock.patch.object(capability_api, "active_tier", lambda: "member"):
            yield client, {"X-CSRF-Token": csrf}, home


def test_panel_html_is_served_as_an_opaque_origin_document(con):
    client, hdr, _home = con
    html = "<h1>Generated</h1><script>fetch('/v1/console/settings')</script>"
    r = client.post("/v1/console/panels", headers=hdr, json={
        "id": "gen-panel", "title": "Generated", "html": html, "nav_group": "build", "icon": "Sparkles"})
    assert r.status_code == 200, r.text

    served = client.get("/v1/console/panels/gen-panel/index.html")
    assert served.status_code == 200
    assert served.text == html                                  # positive control: the real document
    assert served.headers["content-type"].startswith("text/html")
    csp = served.headers.get("content-security-policy", "")
    # ``sandbox`` without allow-same-origin: opaque origin, so no session
    # cookie and no credentialed call to the console API - even top-level.
    assert csp == "sandbox allow-scripts", csp
    assert "allow-same-origin" not in csp
    assert served.headers.get("x-content-type-options") == "nosniff"
    assert served.headers.get("cache-control") == "no-store"


def test_every_route_returning_panel_html_sends_the_csp():
    """Structure: no handler in the console returns stored panel HTML without
    the shared header set. Positive control: ``serve_panel`` is found."""
    src = (_ROUTES / "panels.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    html_returns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "HTMLResponse"
    ]
    assert len(html_returns) >= 1
    for call in html_returns:
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert kw.get("headers") == "PANEL_HTML_HEADERS", ast.unparse(call)
    # No other console module serves from the panel store.
    console = _ROUTES.parent
    others = [
        p for p in console.rglob("*.py")
        if "console_panels" in p.read_text(encoding="utf-8", errors="replace") and p.name != "panels.py"
    ]
    for p in others:
        assert "HTMLResponse" not in p.read_text(encoding="utf-8", errors="replace"), p


# ───────────────────────────── R2-CON-6: scan_window ─────────────────────────


class TestScanWindow:
    def test_small_chain_is_complete(self, tmp_path):
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": i, "ts": 100.0 + i} for i in range(5)])
        w = chain_tail.scan_window(p)
        assert w == {"truncated": False, "scanned_bytes": p.stat().st_size,
                     "total_bytes": p.stat().st_size, "covers_since": None}

    def test_truncated_window_names_what_it_covers(self, tmp_path):
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": i, "ts": 1_700_000_000.0 + i, "pad": "x" * 40} for i in range(200)])
        w = chain_tail.scan_window(p, max_scan_bytes=SMALL)
        first = next(chain_tail.iter_chain_tail(p, max_scan_bytes=SMALL))
        assert w["truncated"] is True
        assert w["scanned_bytes"] == SMALL and w["total_bytes"] == p.stat().st_size
        assert first["i"] > 0
        assert w["covers_since"].startswith("2023-11-14T22:1") and w["covers_since"].endswith("Z")

    def test_missing_file_is_an_empty_complete_window(self, tmp_path):
        assert chain_tail.scan_window(tmp_path / "absent.jsonl") == {
            "truncated": False, "scanned_bytes": 0, "total_bytes": 0, "covers_since": None}

    def test_scan_window_has_production_callers(self):
        """The finding: the truncation probe existed and nothing called it."""
        callers = [
            p.relative_to(_ROUTES).as_posix() for p in _ROUTES.rglob("*.py")
            # a use, not the import: called directly or handed to to_thread
            if any(isinstance(n, ast.Name) and n.id == "scan_window"
                   for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))))
        ]
        assert set(callers) >= {
            "license.py", "phase3_monitor.py", "vibe_engineering.py", "talent.py",
            "vibe/audit_graph.py", "vibe/context_inspector.py",
        }, callers


class TestPanelsReportAPartialScan:
    def test_vibe_traces_and_audit(self, tmp_path, monkeypatch):
        from corvin_console.routes import vibe_engineering as V
        monkeypatch.setattr(V, "_audit_path", lambda tid: _chain_under(tmp_path, tid))
        chain = _chain_under(tmp_path, "_default")
        _write_chain(chain, [
            {"event_type": "skill.executed", "ts": 10.0 + i, "event_id": f"e{i}",
             "tenant_id": "_default", "hash": "a" * 64, "prev_hash": "b" * 64, "details": {}}
            for i in range(60)
        ] + [_cel(100.0 + i) for i in range(40)])
        c = _client(V.router)

        # whole chain inside the window: complete
        body = c.get("/vibe-engineering/audit?limit=1000").json()
        assert body["scan"]["truncated"] is False and body["hasMore"] is False
        assert c.get("/vibe-engineering/traces?limit=500").json()["scan"]["truncated"] is False

        _force_small_window(monkeypatch, V, "iter_chain_tail", "scan_window")
        body = c.get("/vibe-engineering/audit?limit=1000").json()
        assert 0 < len(body["events"]) < 100
        assert body["scan"]["truncated"] is True and body["hasMore"] is True
        assert body["scan"]["total_bytes"] == chain.stat().st_size
        assert body["scan"]["scanned_bytes"] == SMALL
        assert body["scan"]["covers_since"]
        # a "newest N" query that filled N is complete, however large the chain
        full = c.get("/vibe-engineering/audit?limit=1").json()
        assert len(full["events"]) == 1 and full["scan"]["truncated"] is False

        traces = c.get("/vibe-engineering/traces?limit=500").json()
        assert traces["scan"]["truncated"] is True

    def test_phase3_monitor(self, tmp_path, monkeypatch):
        from corvin_console.routes import phase3_monitor as P
        monkeypatch.setattr(P, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        now = time.time()
        _write_chain(_chain_under(tmp_path, "_default"), [
            {"event_type": "routing_decision_made", "ts": now - 3600 + i,
             "details": {"confidence": 0.9, "latency_ms": 5, "complexity_level": "simple",
                         "model_routed": "m"}}
            for i in range(120)
        ])
        c = _client(P.router)
        path = _route_path(P.router, "get_phase3_performance")

        whole = c.get(path).json()
        assert whole["metrics"]["decisions_made"] == 120
        assert whole["scan"]["truncated"] is False and whole["coverage_note"] is None

        _force_small_window(monkeypatch, P, "iter_chain_tail", "scan_window")
        part = c.get(path).json()
        assert 0 < part["metrics"]["decisions_made"] < 120
        assert part["scan"]["truncated"] is True
        assert "Partial result" in part["coverage_note"] and "24h" in part["coverage_note"]

    def test_phase3_monitor_window_older_than_the_cutoff_is_not_partial(self, tmp_path, monkeypatch):
        """A truncated FILE is not a truncated ANSWER when the scanned tail
        already reaches back past the requested time window."""
        from corvin_console.routes import phase3_monitor as P
        monkeypatch.setattr(P, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        now = time.time()
        old = [{"event_type": "noise", "ts": now - 90 * 86400 + i, "pad": "x" * 60} for i in range(200)]
        _write_chain(_chain_under(tmp_path, "_default"), old)
        _force_small_window(monkeypatch, P, "iter_chain_tail", "scan_window")
        body = _client(P.router).get(_route_path(P.router, "get_phase3_performance")).json()
        assert body["scan"]["total_bytes"] > SMALL
        assert body["scan"]["truncated"] is False and body["coverage_note"] is None

    def test_context_inspector(self, tmp_path, monkeypatch):
        from corvin_console.routes.vibe import context_inspector as C
        monkeypatch.setattr(C, "_audit_chain_path", lambda tid: _chain_under(tmp_path, tid))
        _write_chain(_chain_under(tmp_path, "_default"), [
            {"event_type": "x.started", "task_id": f"t{i}", "timestamp_utc": f"2026-11-19T10:{i:02d}:00Z",
             "ts": 1_700_000_000.0 + i}
            for i in range(50)
        ])
        c = _client(C.router)
        listed = c.get(_route_path(C.router, "list_tasks"), params={"limit": 100}).json()
        assert listed["total"] == 50 and listed["scan"]["truncated"] is False

        _force_small_window(monkeypatch, C, "iter_chain_tail", "scan_window")
        listed = c.get(_route_path(C.router, "list_tasks"), params={"limit": 100}).json()
        assert 0 < listed["total"] < 50
        assert listed["scan"]["truncated"] is True
        # t0 is older than the window: the placeholder is LABELLED, with the reason.
        ctx = c.get(_route_path(C.router, "get_task_context_layers").replace("{task_id}", "t0")).json()
        assert ctx["source"] == "example" and ctx["scan"]["truncated"] is True
        seen = c.get(_route_path(C.router, "get_task_context_layers").replace("{task_id}", "t49")).json()
        assert seen["source"] == "audit"

    def test_audit_graph(self, tmp_path, monkeypatch):
        from corvin_console.routes.vibe import audit_graph as G
        chain = _chain_under(tmp_path, "_default")
        monkeypatch.setattr(G, "_find_audit_chain", lambda tid: chain if chain.exists() else None)
        _write_chain(chain, [
            {"event_type": "boot.ok", "ts": 100.0 + i, "hash": f"{i:064d}",
             "prev_hash": f"{i - 1:064d}", "details": {}}
            for i in range(40)
        ])
        path = _route_path(G.router, "get_audit_graph")
        c = _client(G.router)
        assert c.get(path, params={"limit": 1000}).json()["scan"]["truncated"] is False
        _force_small_window(monkeypatch, G, "iter_chain_tail", "scan_window")
        body = c.get(path, params={"limit": 1000}).json()
        assert 0 < body["total_events"] < 40 and body["scan"]["truncated"] is True
        assert c.get(path, params={"limit": 1}).json()["scan"]["truncated"] is False

    def test_talent(self, tmp_path, monkeypatch):
        from corvin_console.routes import talent as T
        monkeypatch.setattr(T, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        _write_chain(_chain_under(tmp_path, "_default"), [_cel(100.0 + i) for i in range(50)])
        c = _client(T.router)
        for path in ("/talent/score", "/talent/history", "/talent/task-types",
                     "/talent/correlation", "/talent/insights", "/talent/story"):
            body = c.get(path).json()
            assert body["scan"]["truncated"] is False, path
        _force_small_window(monkeypatch, T, "iter_chain_tail", "scan_window")
        body = c.get("/talent/score").json()
        assert body["scan"]["truncated"] is True and body["scan"]["covers_since"]


class TestLicenseAuditTailCoverage:
    @pytest.fixture()
    def L(self, tmp_path, monkeypatch):
        from corvin_console.routes import license as L
        chain = _chain_under(tmp_path, "_default")
        monkeypatch.setattr(L.console_audit, "_audit_path", lambda tid: chain)
        monkeypatch.setattr(L, "_check_license_plugin", lambda: None)
        # Two rare licence events, then enough ordinary traffic to push them
        # out of a small "default" tail window.
        _write_chain(chain, (
            [{"event_type": "license.loaded", "ts": 5.0 + i, "details": {"tier": "member"}} for i in range(2)]
            + [{"event_type": "os_turn.started", "ts": 50.0 + i, "details": {"n": i}} for i in range(200)]
        ))
        self.chain = chain
        return L

    def test_rare_events_outside_the_default_tail_are_found_by_the_wider_scan(self, L, monkeypatch):
        monkeypatch.setattr(L, "DEFAULT_MAX_SCAN_BYTES", SMALL)
        assert L._license_audit_tail("_default", 10, max_scan_bytes=SMALL) == []   # the old answer
        r = _client(L.router).get(_route_path(L.router, "get_license_audit"))
        assert r.status_code == 200, r.text
        assert [e["event_type"] for e in r.json()] == ["license.loaded", "license.loaded"]
        assert r.headers["x-corvin-scan-truncated"] == "false"
        assert int(r.headers["x-corvin-scan-total-bytes"]) == self.chain.stat().st_size

    def test_a_chain_larger_than_the_widest_scan_is_reported(self, L, monkeypatch):
        monkeypatch.setattr(L, "DEFAULT_MAX_SCAN_BYTES", SMALL)
        monkeypatch.setattr(L, "LICENSE_AUDIT_MAX_SCAN_BYTES", SMALL * 2)
        r = _client(L.router).get(_route_path(L.router, "get_license_audit"))
        assert r.status_code == 200 and r.json() == []
        assert r.headers["x-corvin-scan-truncated"] == "true"
        assert int(r.headers["x-corvin-scan-bytes"]) == SMALL * 2
        assert r.headers["x-corvin-scan-covers-since"]

    def test_a_full_page_is_complete_whatever_the_chain_size(self, L, monkeypatch):
        with self.chain.open("a", encoding="utf-8") as fh:
            fh.write('{"event_type": "license.loaded", "ts": 999.0, "details": {}}\n')
        monkeypatch.setattr(L, "DEFAULT_MAX_SCAN_BYTES", SMALL)
        monkeypatch.setattr(L, "LICENSE_AUDIT_MAX_SCAN_BYTES", SMALL)
        r = _client(L.router).get(_route_path(L.router, "get_license_audit"), params={"limit": 1})
        assert [e["timestamp"] for e in r.json()] == [999.0]
        assert r.headers["x-corvin-scan-truncated"] == "false"


# ─────────────────────── R2-CON-11: one chain resolver ───────────────────────


@pytest.mark.parametrize("rel", ("vibe/audit_graph.py", "vibe/context_inspector.py", "vibe_engineering.py"))
def test_the_chain_path_is_resolved_never_composed(rel):
    src = (_ROUTES / rel).read_text(encoding="utf-8")
    assert "tenant_audit_chain(" in src                       # positive control
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert '/ "forge" / "audit.jsonl"' not in code, rel


def test_the_vibe_sub_routes_read_the_canonical_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    from forge import paths as forge_paths
    from corvin_console.routes.vibe import audit_graph as G, context_inspector as C

    canonical = Path(forge_paths.tenant_audit_chain("acme"))
    assert C._audit_chain_path("acme") == canonical
    assert G._find_audit_chain("acme") is None                # absent: no chain
    _write_chain(canonical, [{"event_type": "boot.ok", "ts": 1.0, "hash": "0" * 64}])
    assert G._find_audit_chain("acme") == canonical
