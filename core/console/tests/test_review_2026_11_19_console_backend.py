"""Review 2026-11-19, console backend (CON-1 .. CON-8).

What is pinned here:

* CON-1  ``routes/deprecated_api_metrics`` is not mounted, stays importable,
         compares aware datetimes and reports no invented availability figure.
* CON-2  the panels that scan the audit chain do it off the event loop, line
         by line, bounded to the tail (``chain_tail``) - structurally (AST)
         and at run time (the scan runs on a worker thread).
* CON-4  the Phase 3 monitor reads the SESSION tenant's chain.
* CON-6  ``settings.py`` / ``license.py`` write the audit chain from async
         handlers only through an awaited ``asyncio.to_thread``.
* CON-8  ``/intents/recent`` says ``available: false`` instead of a bare ``[]``.

Every chain file lives under a temp directory; nothing touches CORVIN_HOME.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import chain_tail  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402

_ROUTES = _REPO / "core" / "console" / "corvin_console" / "routes"


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            values[f.name] = "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


def _client(router, tenant: str = "_default") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_record(tenant)
    app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
        lambda: _fake_record(tenant))
    return TestClient(app)


def _route_path(router, endpoint_name: str) -> str:
    return next(r.path for r in router.routes if getattr(r, "name", "") == endpoint_name)


def _write_chain(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _chain_under(base: Path, tenant: str) -> Path:
    return base / tenant / "global" / "forge" / "audit.jsonl"


# ───────────────────────────── chain_tail ────────────────────────────────────


class TestChainTail:
    def test_reads_whole_small_file_in_order(self, tmp_path):
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": i} for i in range(5)])
        assert [r["i"] for r in chain_tail.iter_chain_tail(p)] == [0, 1, 2, 3, 4]
        assert chain_tail.scan_truncated(p) is False

    def test_tail_window_drops_old_records_and_the_cut_line(self, tmp_path):
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": i, "pad": "x" * 50} for i in range(200)])
        line_len = len(json.dumps({"i": 0, "pad": "x" * 50})) + 1
        # A window that starts mid-line: that partial line must not be parsed.
        window = line_len * 10 + 7
        got = [r["i"] for r in chain_tail.iter_chain_tail(p, max_scan_bytes=window)]
        assert got, "the tail must yield records"
        assert got[-1] == 199
        assert got == list(range(got[0], 200)), "contiguous, oldest first"
        assert 5 <= len(got) <= 11, got
        assert chain_tail.scan_truncated(p, max_scan_bytes=window) is True

    def test_window_starting_exactly_on_a_line_keeps_that_line(self, tmp_path):
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": i} for i in range(10, 20)])  # equal-length lines
        line_len = len(json.dumps({"i": 10})) + 1
        got = [r["i"] for r in chain_tail.iter_chain_tail(p, max_scan_bytes=line_len * 3)]
        assert got == [17, 18, 19]

    def test_needles_skip_and_malformed_lines_are_ignored(self, tmp_path):
        p = tmp_path / "c.jsonl"
        p.write_text(
            '{"event_type": "a"}\nnot json\n\n[1, 2]\n{"event_type": "b"}\n{"event_type": "a"',
            encoding="utf-8",
        )
        assert [r["event_type"] for r in chain_tail.iter_chain_tail(p)] == ["a", "b"]
        assert [r["event_type"] for r in chain_tail.iter_chain_tail(p, needles=('"b"',))] == ["b"]

    def test_missing_file_and_zero_window_yield_nothing(self, tmp_path):
        assert list(chain_tail.iter_chain_tail(tmp_path / "absent.jsonl")) == []
        p = tmp_path / "c.jsonl"
        _write_chain(p, [{"i": 1}])
        assert list(chain_tail.iter_chain_tail(p, max_scan_bytes=0)) == []
        with pytest.raises(ValueError):
            list(chain_tail.iter_chain_tail(p, max_scan_bytes=-1))


# ───────────────────── CON-2 / CON-6: structure (AST) ────────────────────────

#: Calls that read a whole file, or scan the chain, on the calling thread.
_BLOCKING_ATTRS = {"read_text", "read_bytes", "readlines", "splitlines"}
#: Blocking chain readers of the CON-2 modules (callable only via to_thread).
_BLOCKING_READERS = {
    "iter_chain_tail", "_read_decisions", "_stats", "_scan_audit_events",
    "_scan_task_ids", "_load_context_from_audit", "_load_graph",
    "_license_audit_tail", "_read_audit_trail_metrics", "_find_audit_chain",
    "_parse_audit_chain", "_aggregate_metrics",
    # the coverage probes stat + read the chain as well (review round 2)
    "scan_window", "scan_truncated", "_scan_info", "_graph_scan", "_license_audit_scan",
    # review round 3 (R3-NEW-10/11/24/26/30): the SSE tail readers, the maturity
    # builders, the override audit-log reader and the STT availability probe
    "_backfill", "_read_new", "build_measurement", "build_history", "build_anomalies",
    "build_patterns", "get_measurements", "read_audit_log", "_today_scan",
    "_get_stt_available", "is_stt_available",
}
#: handler -> the file reads it is allowed to keep (not a chain scan), with why.
_READ_EXEMPT = {
    # reads ONE content-addressed brief file (sha-named), not the chain
    ("vibe_engineering.py", "explain_brief"),
    # reads the installed licence token (a few hundred bytes), not the chain
    ("license.py", "revoke_license"),
}

_CHAIN_SCAN_MODULES = (
    "license.py", "talent.py", "vibe_engineering.py", "vibe/audit_graph.py",
    "vibe/context_inspector.py", "phase3_monitor.py", "deprecated_api_metrics.py",
    # Review round 2 (R2-CON-4): chain writes, flock, RSA key generation and
    # cost-file reads ran in ``async def`` handlers. They are plain ``def``
    # now (threadpool); a handler turned back into ``async def`` is caught
    # here as soon as it reads, and by tests/unit/licensing/
    # test_review_r2_licensing.py::test_no_route_handler_of_the_two_modules_is_async.
    "a2a_licensing_gate_routes.py", "cost_insights_routes.py",
    # Review round 3: the audit SSE stream (R3-NEW-10), the maturity dashboard
    # routes (R3-NEW-11), the landing dashboard and the dual-track view
    # (R3-NEW-24, plain ``def`` handlers), multi-instance sync (R3-NEW-25,
    # plain ``def``), the override routes (R3-NEW-26), the RAG hub and the
    # voice-summary routes (R3-NEW-30). Behaviour is pinned in
    # test_review_r3_console_routes.py.
    "streams.py", "api_vibe_maturity.py", "api_vibe_maturity_phase3.py",
    "dashboard.py", "chain_dual_track.py", "multi_instance.py",
    "control_plane_overrides.py", "rag_hub.py", "voice_summary.py",
)


def _direct_calls(fn: ast.AsyncFunctionDef):
    """Calls made on the handler's own (event-loop) frame: nested ``def``s and
    lambdas are skipped, they run wherever they are invoked."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def _async_handlers(rel: str):
    tree = ast.parse((_ROUTES / rel).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]


def _blocking_reads(rel: str) -> list[str]:
    found = []
    for fn in _async_handlers(rel):
        if (rel, fn.name) in _READ_EXEMPT:
            continue
        for call in _direct_calls(fn):
            f = call.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in _BLOCKING_READERS or name in _BLOCKING_ATTRS or name == "open":
                found.append(f"{rel}:{call.lineno} {fn.name} -> {name}()")
    return found


@pytest.mark.parametrize("rel", _CHAIN_SCAN_MODULES)
def test_no_async_handler_scans_the_chain_on_the_event_loop(rel):
    assert _blocking_reads(rel) == []


def test_the_ast_check_does_catch_a_sync_scan(tmp_path, monkeypatch):
    """Positive control: the scanner above must flag the shapes it replaces."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "async def h(p):\n"
        "    for ln in p.read_text().splitlines():\n"
        "        pass\n"
        "    with open(p) as f:\n"
        "        pass\n"
        "    return _read_decisions('t', 5)\n"
        "async def ok(p):\n"
        "    import asyncio\n"
        "    return await asyncio.to_thread(_read_decisions, 't', 5)\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "_ROUTES", tmp_path)
    hits = _blocking_reads("bad.py")
    assert len(hits) == 4, hits
    assert all(" h -> " in h for h in hits), hits


@pytest.mark.parametrize("rel", ("settings.py", "license.py"))
def test_async_handlers_write_the_audit_chain_only_through_to_thread(rel):
    """A direct ``console_audit.*`` / ``_license_audit.*`` call in an async
    handler is a chain write (lock + fsync) on the event loop."""
    offenders = []
    for fn in _async_handlers(rel):
        for call in _direct_calls(fn):
            f = call.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in ("console_audit", "_license_audit")):
                offenders.append(f"{rel}:{call.lineno} {fn.name} -> {f.value.id}.{f.attr}()")
    assert offenders == []


def test_those_audit_writes_still_exist_and_are_awaited():
    """Positive control for the test above: the writes were moved, not removed,
    and every ``asyncio.to_thread(console_audit.x, ...)`` is awaited."""
    for rel, minimum in (("settings.py", 5), ("license.py", 15)):
        tree = ast.parse((_ROUTES / rel).read_text(encoding="utf-8"))
        awaited = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Await)}
        moved = []
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and ast.unparse(n.func) == "asyncio.to_thread" and n.args):
                continue
            target = ast.unparse(n.args[0])
            if target.startswith(("console_audit.", "_license_audit.")):
                moved.append(target)
                assert id(n) in awaited, f"{rel}:{n.lineno} to_thread({target}) is not awaited"
        assert len(moved) >= minimum, (rel, len(moved))


# ───────────────────── CON-2: run time (worker thread) ───────────────────────


class _OffLoopProbe(list):
    """Records, per call, whether an event loop was running on the calling
    thread. ``threading.main_thread()`` proves nothing here: the TestClient
    runs the app's loop on its own portal thread."""

    def off_loop(self) -> bool:
        return bool(self) and not any(self)


def _spy_thread(monkeypatch, module, name) -> _OffLoopProbe:
    """Replace ``module.name`` with a wrapper recording, for each call, True
    when it ran ON an event-loop thread (i.e. blocked the loop)."""
    seen = _OffLoopProbe()
    real = getattr(module, name)

    def _wrapped(*a, **kw):
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return real(*a, **kw)

    monkeypatch.setattr(module, name, _wrapped)
    return seen


def test_the_off_loop_probe_detects_a_scan_on_the_event_loop(monkeypatch):
    """Positive control for every ``off_loop()`` assertion below."""
    import types

    from fastapi import APIRouter

    mod = types.SimpleNamespace(scan=lambda: 1)
    seen = _spy_thread(monkeypatch, mod, "scan")
    router = APIRouter()

    @router.get("/on-loop")
    async def on_loop():
        return {"v": mod.scan()}

    @router.get("/off-loop")
    async def off_loop():
        return {"v": await asyncio.to_thread(mod.scan)}

    c = _client(router)
    assert c.get("/on-loop").status_code == 200
    assert list(seen) == [True] and seen.off_loop() is False
    seen.clear()
    assert c.get("/off-loop").status_code == 200
    assert list(seen) == [False] and seen.off_loop() is True
    assert _OffLoopProbe().off_loop() is False, "no call recorded is not a pass"


def _cel(ts: float, top: float = 0.8, tenant: str | None = None) -> dict:
    rec = {
        "event_type": "cel.decision", "ts": ts, "hash": "h" * 64,
        "details": {"top_score": top, "degraded": None, "session_id": "s1", "stages": [
            {"stage": "memory", "status": "ok", "sources": [{"id": "m1", "score": top}]}]},
    }
    if tenant:
        rec["tenant_id"] = tenant
    return rec


class TestTalentOffLoop:
    def test_scan_runs_on_a_worker_thread_and_is_tail_bounded(self, tmp_path, monkeypatch):
        from corvin_console.routes import talent as T
        monkeypatch.setattr(T, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        _write_chain(_chain_under(tmp_path, "_default"), [_cel(100.0 + i) for i in range(50)])
        seen = _spy_thread(monkeypatch, T, "_read_decisions")

        body = _client(T.router).get("/talent/score").json()
        assert body["empty"] is False
        assert seen.off_loop(), list(seen)

        full = T._read_decisions("_default")
        assert len(full) == 50
        newest = T._read_decisions("_default", limit=3)
        assert [d["ts"] for d in newest] == [147.0, 148.0, 149.0], "newest N, oldest first"
        tail = T._read_decisions("_default", max_scan_bytes=400)
        assert 0 < len(tail) < 50 and tail[-1]["ts"] == 149.0


class TestVibeEngineeringOffLoop:
    @pytest.fixture()
    def V(self, tmp_path, monkeypatch):
        from corvin_console.routes import vibe_engineering as V
        monkeypatch.setattr(V, "_audit_path", lambda tid: _chain_under(tmp_path, tid))
        return V

    def test_traces_scan_off_loop_newest_first(self, V, tmp_path, monkeypatch):
        _write_chain(_chain_under(tmp_path, "_default"), [_cel(100.0 + i) for i in range(20)])
        seen = _spy_thread(monkeypatch, V, "_read_decisions")
        r = _client(V.router).get("/vibe-engineering/traces?limit=5")
        assert r.status_code == 200, r.text
        assert seen.off_loop(), list(seen)
        got = V._read_decisions("_default", 3)
        assert len(got) == 3
        assert V._read_decisions("_default", 0) == []
        assert len(V._read_decisions("_default", 500, max_scan_bytes=600)) < 20

    def test_audit_query_is_off_loop_clamped_and_tenant_scoped(self, V, tmp_path, monkeypatch):
        records = [
            {"event_type": "skill.executed", "ts": 10.0 + i, "event_id": f"e{i}",
             "tenant_id": "_default", "hash": "a" * 64, "prev_hash": "b" * 64,
             "details": {"skill_id": "os.x"}}
            for i in range(30)
        ]
        records.insert(3, {"event_type": "skill.executed", "ts": 11.5, "event_id": "foreign",
                           "tenant_id": "other", "details": {}})
        _write_chain(_chain_under(tmp_path, "_default"), records)
        seen = _spy_thread(monkeypatch, V, "_scan_audit_events")

        c = _client(V.router)
        body = c.get("/vibe-engineering/audit?limit=5").json()
        assert seen.off_loop(), list(seen)
        ids = [e["id"] for e in body["events"]] if "events" in body else None
        direct = V._scan_audit_events(
            _chain_under(tmp_path, "_default"), "_default",
            since_ts=None, until_ts=None, wanted_types=None, wanted_skills=None, limit=5,
        )
        assert [e["id"] for e in direct] == ["e25", "e26", "e27", "e28", "e29"]
        if ids is not None:
            assert ids == [e["id"] for e in direct]
        everything = V._scan_audit_events(
            _chain_under(tmp_path, "_default"), "_default",
            since_ts=None, until_ts=None, wanted_types=None, wanted_skills=None, limit=1000,
        )
        assert len(everything) == 30 and "foreign" not in {e["id"] for e in everything}
        # limit=0 used to slice ``events[-0:]`` = the whole chain.
        assert V._scan_audit_events(
            _chain_under(tmp_path, "_default"), "_default",
            since_ts=None, until_ts=None, wanted_types=None, wanted_skills=None, limit=0,
        ) == []
        r0 = c.get("/vibe-engineering/audit?limit=0")
        if r0.status_code == 200 and "events" in r0.json():
            assert len(r0.json()["events"]) <= 1


class TestVibeSubRoutesOffLoop:
    def test_context_inspector(self, tmp_path, monkeypatch):
        from corvin_console.routes.vibe import context_inspector as C
        monkeypatch.setattr(C, "_audit_chain_path", lambda tid: _chain_under(tmp_path, tid))
        _write_chain(_chain_under(tmp_path, "_default"), [
            {"event_type": "x.started", "task_id": "t1", "timestamp_utc": "2026-11-19T10:00:00Z"},
            {"event_type": "noise"},
            {"event_type": "x.done", "task_id": "t1", "timestamp_utc": "2026-11-19T10:01:00Z"},
            {"event_type": "y.started", "task_id": "t2", "timestamp_utc": "2026-11-19T11:00:00Z"},
        ])
        seen_scan = _spy_thread(monkeypatch, C, "_scan_task_ids")
        seen_load = _spy_thread(monkeypatch, C, "_load_context_from_audit")

        assert set(C._scan_task_ids("_default")) == {"t1", "t2"}
        assert C._scan_task_ids("_default")["t1"]["event_type"] == "x.started"
        assert C._load_context_from_audit("_default", "absent") is None
        seen_scan.clear(); seen_load.clear()

        c = _client(C.router)
        listed = c.get(_route_path(C.router, "list_tasks"))
        assert listed.status_code == 200, listed.text
        assert seen_scan.off_loop(), list(seen_scan)
        ctx = c.get(_route_path(C.router, "get_task_context_layers").replace("{task_id}", "t1"))
        assert ctx.status_code == 200, ctx.text
        assert seen_load.off_loop(), list(seen_load)

    def test_audit_graph_returns_the_newest_records(self, tmp_path, monkeypatch):
        from corvin_console.routes.vibe import audit_graph as G
        chain = _chain_under(tmp_path, "_default")
        monkeypatch.setattr(G, "_find_audit_chain", lambda tid: chain if chain.exists() else None)
        assert G._load_graph("_default", 10, None, None) is None
        _write_chain(chain, [
            {"event_type": "boot.ok", "ts": 100.0 + i, "hash": f"{i:064d}",
             "prev_hash": f"{i - 1:064d}", "details": {}}
            for i in range(40)
        ])
        seen = _spy_thread(monkeypatch, G, "_load_graph")
        total, nodes, _edges, _crit, _anom = G._load_graph("_default", 7, None, None)
        assert total == 7
        seen.clear()

        r = _client(G.router).get(_route_path(G.router, "get_audit_graph"), params={"limit": 7})
        assert r.status_code == 200, r.text
        assert r.json()["total_events"] == 7
        assert seen.off_loop(), list(seen)


class TestLicenseAuditTail:
    def test_newest_first_filtered_and_bounded(self, tmp_path, monkeypatch):
        from corvin_console.routes import license as L
        chain = _chain_under(tmp_path, "_default")
        monkeypatch.setattr(L.console_audit, "_audit_path", lambda tid: chain)
        _write_chain(chain, (
            [{"event_type": "license.loaded", "ts": float(i), "details": {"tier": "member", "secret": "no"}}
             for i in range(1, 9)]
            + [{"event_type": "os_turn.started", "ts": 50.0, "details": {"note": "license. mention"}}]
        ))
        got = L._license_audit_tail("_default", 3)
        assert [e.timestamp for e in got] == [8.0, 7.0, 6.0]
        assert all(e.event_type == "license.loaded" for e in got)
        assert all("secret" not in e.details for e in got)
        assert L._license_audit_tail("../etc", 3) == []
        assert L._license_audit_tail("_default", 3) == got

    def test_handler_awaits_the_scan_off_the_loop(self, tmp_path, monkeypatch):
        from corvin_console.routes import license as L
        chain = _chain_under(tmp_path, "_default")
        monkeypatch.setattr(L.console_audit, "_audit_path", lambda tid: chain)
        monkeypatch.setattr(L, "_check_license_plugin", lambda: None)
        _write_chain(chain, [{"event_type": "license.loaded", "ts": 5.0, "details": {}}])
        seen = _spy_thread(monkeypatch, L, "_license_audit_tail")
        r = _client(L.router).get(_route_path(L.router, "get_license_audit"))
        assert r.status_code == 200, r.text
        assert seen.off_loop(), list(seen)
        assert [e["event_type"] for e in r.json()] == ["license.loaded"]


# ───────────────────────────── CON-4: phase 3 ────────────────────────────────


class TestPhase3MonitorTenantChain:
    @pytest.fixture()
    def P(self, tmp_path, monkeypatch):
        from corvin_console.routes import phase3_monitor as P
        monkeypatch.setattr(P, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        return P

    @staticmethod
    def _decision(ts, **details):
        return {"event_type": "routing_decision_made", "ts": ts, "details": details}

    def test_reads_the_session_tenants_chain_only(self, P, tmp_path, monkeypatch):
        now = time.time()
        _write_chain(_chain_under(tmp_path, "acme"), [
            self._decision(now - 60, confidence=0.9, complexity_level="simple",
                           model_routed="haiku", latency_ms=10),
            self._decision(now - 30, confidence=0.7, complexity_level="complex",
                           model_routed="opus", latency_ms=30),
            {"event_type": "routing_decision_error", "ts": now - 20, "details": {}},
            # too old for the 24 h window
            self._decision(now - 3 * 86400, confidence=0.1, latency_ms=999),
            # names another tenant inside this file: dropped
            {**self._decision(now - 10, confidence=0.1), "tenant_id": "other"},
        ])
        _write_chain(_chain_under(tmp_path, "_default"), [
            self._decision(now - 5, confidence=0.2, model_routed="sonnet", latency_ms=1),
        ])
        seen = _spy_thread(monkeypatch, P, "_read_audit_trail_metrics")

        acme = _client(P.router, "acme").get("/monitor/phase3-performance").json()
        dflt = _client(P.router, "_default").get("/monitor/phase3-performance").json()
        assert seen.off_loop(), list(seen)

        m = P._read_audit_trail_metrics("acme", 24)
        assert m["decisions_made"] == 2 and m["error_count"] == 1
        assert sorted(m["confidence_scores"]) == [0.7, 0.9]
        assert set(m["by_model"]) == {"haiku", "opus"}
        assert P._read_audit_trail_metrics("_default", 24)["decisions_made"] == 1
        assert P._read_audit_trail_metrics("nobody", 24)["decisions_made"] == 0
        assert acme != dflt, "two tenants must not see the same aggregate"

    def test_never_reads_the_home_directory_chain(self, P):
        src = (_ROUTES / "phase3_monitor.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "Path.home" not in calls
        assert "tenant_audit_chain" in calls

    def test_iso_timestamp_shape_and_tail_bound(self, P, tmp_path):
        now = datetime.now(timezone.utc)
        _write_chain(_chain_under(tmp_path, "_default"), [
            {"event_type": "routing_decision_made",
             "timestamp_utc": (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "confidence": 0.5, "latency_ms": 5, "model_routed": "haiku"}
            for i in range(40, 0, -1)
        ])
        assert P._read_audit_trail_metrics("_default", 24)["decisions_made"] == 40
        bounded = P._read_audit_trail_metrics("_default", 24, max_scan_bytes=500)
        assert 0 < bounded["decisions_made"] < 40


# ───────────────────────────── CON-8: intents ────────────────────────────────


def test_intents_recent_says_unavailable_instead_of_an_empty_list():
    from corvin_console.routes import intents as I
    r = _client(I.router, "acme").get("/intents/recent?limit=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["events"] == []
    assert isinstance(body["reason"], str) and body["reason"]
    assert body["tenant_id"] == "acme"
    assert _client(I.router).get("/intents/recent?limit=0").status_code == 422


# ───────────────────────── CON-1: deprecated metrics ─────────────────────────


class TestDeprecatedApiMetricsUnmounted:
    def test_router_is_not_mounted_on_the_console_app(self):
        import corvin_console.app as console_app

        def _paths(routes):
            for r in routes:
                p = getattr(r, "path", None)
                if isinstance(p, str):
                    yield p
                inner = getattr(r, "original_router", None) or getattr(r, "router", None)
                if inner is not None:
                    yield from _paths(getattr(inner, "routes", []) or [])
                sub = getattr(r, "routes", None)
                if sub:
                    yield from _paths(sub)

        paths = set(_paths(console_app.router.routes))
        # Positive control: the walk reaches mounted console routes at all.
        assert any("intents" in p for p in paths), sorted(paths)[:20]
        assert any("monitor/phase3-performance" in p for p in paths)
        assert not [p for p in paths if "deprecated-apis" in p]
        src = (_ROUTES.parent / "app.py").read_text(encoding="utf-8")
        assert "deprecated_api_metrics_route" not in src

    def test_module_still_imports_and_compares_aware_datetimes(self, tmp_path, monkeypatch):
        from corvin_console.routes import deprecated_api_metrics as D
        monkeypatch.setattr(D, "tenant_audit_chain", lambda tid: _chain_under(tmp_path, tid))
        now = datetime.now(timezone.utc)

        def _ev(dt, failed=False, tenant="_default", stamp=None):
            return {"event_type": "deprecated_api_call", "tenant_id": tenant,
                    "timestamp": stamp or dt.isoformat().replace("+00:00", "Z"),
                    "details": {"api_name": "get_session_context", "failed": failed}}

        _write_chain(_chain_under(tmp_path, "_default"), [
            _ev(now - timedelta(hours=1)),
            _ev(now - timedelta(hours=2), failed=True),
            # naive stamp: treated as UTC, must not raise
            _ev(now, stamp=(now - timedelta(hours=3)).replace(tzinfo=None).isoformat()),
            _ev(now - timedelta(days=3)),                 # outside the window
            _ev(now - timedelta(minutes=5), tenant="other"),
            _ev(now, stamp="garbage"),
        ])
        # The old code compared an aware event time with a naive utcnow():
        # TypeError, swallowed -> always zero events.
        events = D._parse_audit_chain("_default")
        assert len(events) == 3
        naive_since = (now - timedelta(hours=24)).replace(tzinfo=None)
        assert len(D._parse_audit_chain("_default", naive_since)) == 3

        m = D._aggregate_metrics("_default")
        assert m["total_calls"] == 3 and m["error_count"] == 1
        assert m["error_rate_pct"] == pytest.approx(100 / 3)
        assert "skill_availability_pct" not in m
        assert all("latency" not in k and "availability" not in k
                   for row in m["per_api"].values() for k in row)

    def test_no_invented_availability_or_latency_remains(self):
        from corvin_console.routes import deprecated_api_metrics as D
        src = (_ROUTES / "deprecated_api_metrics.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        numbers = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, float)}
        assert 99.99 not in numbers
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "datetime.utcnow" not in calls and "hash" not in calls
        for model in (D.CurrentMetricsResponse, D.BaselineReport, D.TrendDataPoint,
                      D.DeprecatedAPICallCount):
            assert not [f for f in model.model_fields if "availability" in f or "latency" in f]
