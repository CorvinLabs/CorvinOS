"""Review 2026-11-19, round 3 - console routes (R3-NEW-5 .. R3-NEW-30).

What is pinned here, each through the real route where one exists:

* R3-NEW-5   custom-engine ping: no arbitrary env var leaves the process, only
             http/https, the L35 gate is asked, redirects are not followed.
* R3-NEW-9   tool preview: minimal environment, throwaway cwd, owner gate.
* R3-NEW-10  audit SSE stream: bounded backfill off the loop, no lost line.
* R3-NEW-11  maturity dashboard: incremental per-tenant chain index, off the loop.
* R3-NEW-24  dashboard / dual-track: no full-chain scan per request.
* R3-NEW-25  multi-instance: sync handlers, tenant-bound vault items.
* R3-NEW-26  overrides: honest 501, counted corrupt lines, threaded writes.
* R3-NEW-17/18 layer forge: owner gate, override audited only when it happens.
* R3-NEW-20  file upload does not write through a symlink.
* R3-NEW-21  unsigned webhook posts do not spend the channel's budget.
* R3-NEW-22  archive intake: bounded read, entry/size caps, strict manifest.
* R3-NEW-30  voice health is not answered by a mock.

Every chain and every tenant directory lives under ``tmp_path``.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import hmac
import http.server
import io
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402

_ROUTES = _REPO / "core" / "console" / "corvin_console" / "routes"


def _fake_record(tenant_id: str = "_default", tier: str = "owner") -> session_auth.SessionRecord:
    now = time.time()
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
            values[f.name] = tier
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


def _client(router, tenant: str = "_default", tier: str = "owner", extra_overrides=()) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    rec = _fake_record(tenant, tier)
    for dep in (console_deps.require_session, console_deps.require_csrf,
                console_deps.require_session_csrf_on_mutation, *extra_overrides):
        app.dependency_overrides[dep] = lambda rec=rec: rec
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private CORVIN_HOME; every route below resolves its tenant paths here."""
    h = tmp_path / "corvin_home"
    (h / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    return h


def _chain(home: Path, tenant: str = "_default") -> Path:
    p = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(path: Path, records) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _silence_audit(monkeypatch, module) -> list[tuple[str, dict]]:
    """Replace a route module's ``console_audit`` writers with a recorder that
    also notes the thread each write ran on."""
    calls: list[tuple[str, dict]] = []

    class _Rec:
        def __getattr__(self, name):
            def _w(*a, **kw):
                calls.append((name, {**kw, "_thread": threading.current_thread().name}))
            return _w

    monkeypatch.setattr(module, "console_audit", _Rec())
    return calls


class _Probe(http.server.BaseHTTPRequestHandler):
    """Tiny local HTTP endpoint that records what reached it."""
    seen: list[dict] = []
    redirect_to: str | None = None

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if type(self).redirect_to:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.end_headers()
            return
        body = b'{"data": [{"id": "m1"}, {"id": "m2"}]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: D102
        pass


@pytest.fixture
def probe():
    handler = type("P", (_Probe,), {"seen": [], "redirect_to": None})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield handler, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


# ───────────────────────── R3-NEW-5: custom engine ping ──────────────────────


class TestCustomEnginePing:
    @pytest.fixture
    def ce(self, home, monkeypatch):
        from corvin_console.routes import custom_engines as mod
        _silence_audit(monkeypatch, mod)
        return mod, _client(mod.router)

    def _body(self, base_url, **kw):
        return {"display_name": "x", "transport": "openai_compat", "base_url": base_url,
                "locality": "local", **kw}

    def test_registration_refuses_an_arbitrary_env_var(self, ce, probe):
        mod, client = ce
        _h, url = probe
        r = client.put("/engines/custom/evil", json=self._body(url, auth_env="ANTHROPIC_API_KEY"))
        assert r.status_code == 400, r.text
        assert "CORVIN_ENGINE_" in r.text
        assert mod._load_engine("_default", "evil") is None

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd#", "ftp://example.com/x", "http://user:pw@example.com/v1", "http:///nohost",
    ])
    def test_registration_refuses_non_http_urls(self, ce, url):
        _mod, client = ce
        r = client.put("/engines/custom/e1", json=self._body(url))
        assert r.status_code == 400, r.text

    def test_a_legacy_manifest_never_leaks_the_named_env_var(self, ce, probe, monkeypatch):
        """A manifest stored BEFORE the registration check: the ping must not
        read the arbitrary variable it names."""
        mod, client = ce
        handler, url = probe
        monkeypatch.setenv("FAKE_PLATFORM_SECRET", "s3cr3t-value-from-process-env")
        mod._write_engine("_default", "legacy", {
            "engine_id": "legacy", "transport": "openai_compat", "_base_url": url,
            "auth_env": "FAKE_PLATFORM_SECRET", "locality": "local"})
        r = client.post("/engines/custom/legacy/ping")
        assert r.status_code == 200 and r.json()["reachable"] is True, r.text
        assert handler.seen and all(s["auth"] is None for s in handler.seen), handler.seen

    def test_positive_control_an_engine_credential_is_sent(self, ce, probe, monkeypatch):
        mod, client = ce
        handler, url = probe
        monkeypatch.setenv("CORVIN_ENGINE_LOCAL_KEY", "engine-token")
        assert client.put("/engines/custom/ok", json=self._body(
            url, auth_env="CORVIN_ENGINE_LOCAL_KEY")).status_code == 200
        r = client.post("/engines/custom/ok/ping")
        assert r.json() == {"ok": True, "engine_id": "ok", "reachable": True, "model_count": 2}
        assert handler.seen[-1]["auth"] == "Bearer engine-token"

    def test_file_url_in_a_stored_manifest_is_not_opened(self, ce, tmp_path):
        mod, client = ce
        target = tmp_path / "probe.json"
        target.write_text('{"models": [1, 2, 3]}')
        mod._write_engine("_default", "f", {"engine_id": "f", "transport": "openai_compat",
                                            "_base_url": f"file://{target}#", "locality": "local"})
        r = client.post("/engines/custom/f/ping")
        assert r.json()["reachable"] is False and r.json()["error"] == "scheme_not_allowed", r.text

    def test_redirects_are_not_followed(self, ce, probe):
        mod, client = ce
        handler, url = probe
        second = type("P2", (_Probe,), {"seen": [], "redirect_to": None})
        srv2 = http.server.HTTPServer(("127.0.0.1", 0), second)
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            handler.redirect_to = f"http://127.0.0.1:{srv2.server_port}/models"
            assert client.put("/engines/custom/r", json=self._body(url)).status_code == 200
            r = client.post("/engines/custom/r/ping")
            assert r.json()["error"] == "redirect_blocked", r.text
            assert handler.seen and second.seen == []
        finally:
            srv2.shutdown()
            srv2.server_close()

    def test_a_non_local_engine_may_not_point_at_loopback(self, ce, probe):
        mod, client = ce
        handler, url = probe
        mod._write_engine("_default", "c", {"engine_id": "c", "transport": "openai_compat",
                                            "_base_url": url, "locality": "eu_cloud"})
        r = client.post("/engines/custom/c/ping")
        assert r.json()["error"] == "egress_blocked", r.text
        assert handler.seen == []

    def test_link_local_metadata_is_refused_even_for_local(self, ce):
        mod, client = ce
        mod._write_engine("_default", "m", {"engine_id": "m", "transport": "ollama",
                                            "_base_url": "http://169.254.169.254", "locality": "local"})
        assert client.post("/engines/custom/m/ping").json()["error"] == "egress_blocked"

    def test_the_l35_policy_is_asked_before_the_connect(self, ce, probe, home):
        mod, client = ce
        handler, url = probe
        cfg = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        cfg.write_text("spec:\n  egress:\n    enabled: true\n    default_action: deny\n"
                       "    allowed_hosts: [api.example.com]\n", encoding="utf-8")
        os.chmod(cfg, 0o600)
        assert client.put("/engines/custom/d", json=self._body(url)).status_code == 200
        r = client.post("/engines/custom/d/ping")
        assert r.json()["error"] == "egress_blocked", r.text
        assert handler.seen == [], "no request may leave before L35 admits the host"


# ───────────────────────── R3-NEW-9: tool preview ────────────────────────────


class TestToolPreview:
    @pytest.fixture
    def tm(self, home, monkeypatch):
        from corvin_console.routes import tools_manual as mod
        _silence_audit(monkeypatch, mod)
        return mod

    def _tool(self, mod, impl: str) -> None:
        mod._write_tool("_default", "leak", "d", impl, {}, None)

    def test_the_child_sees_no_console_secret_and_a_throwaway_cwd(self, tm, monkeypatch):
        monkeypatch.setenv("FAKE_PLATFORM_SECRET", "s3cr3t-in-console-env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
        self._tool(tm, "import os, json\nprint(json.dumps({'env': sorted(os.environ), "
                       "'cwd': os.getcwd(), 'x': inputs.get('x')}))\n")
        client = _client(tm.router, extra_overrides=(tm.require_forge_capability,))
        r = client.post("/tools/preview", json={"name": "leak", "inputs": {"x": 7}})
        assert r.status_code == 200 and r.json()["exit_code"] == 0, r.text
        out = json.loads(r.json()["stdout"])
        assert out["x"] == 7
        assert "FAKE_PLATFORM_SECRET" not in out["env"] and "ANTHROPIC_API_KEY" not in out["env"]
        assert not [k for k in out["env"] if k.startswith("CORVIN_")], out["env"]
        assert "corvin-tool-preview-" in out["cwd"]
        assert not Path(out["cwd"]).exists(), "the working directory is removed afterwards"

    def test_preview_needs_an_owner_session(self, tm):
        self._tool(tm, "print(1)\n")
        client = _client(tm.router, tier="operator", extra_overrides=(tm.require_forge_capability,))
        assert client.post("/tools/preview", json={"name": "leak", "inputs": {}}).status_code == 403

    def test_preview_carries_the_forge_capability_gate(self, tm):
        route = next(r for r in tm.router.routes if getattr(r, "name", "") == "preview_tool")
        deps = {d.call for d in route.dependant.dependencies}
        assert tm.require_forge_capability in deps

    def test_the_docstrings_do_not_claim_a_sandbox(self, tm):
        assert "bwrap sandbox in production" not in (tm.__doc__ or "")
        assert "NOT sandboxed" in (tm.preview_tool.__doc__ or "")


# ───────────────────────── R3-NEW-10: audit SSE stream ───────────────────────


class TestAuditStream:
    def test_backfill_is_bounded_and_anchors_on_the_last_complete_line(self, tmp_path):
        from corvin_console.routes import streams
        p = tmp_path / "c.jsonl"
        _append(p, [{"ts": float(i), "event_type": "a.b", "severity": "INFO", "i": i}
                    for i in range(300)])
        complete = p.stat().st_size
        with p.open("a") as fh:
            fh.write('{"ts": 999.0, "event_type": "a.half"')  # writer mid-append
        frames, cursor, truncated = streams._backfill(
            p, floor=0.0, severity=None, prefixes=None, history_count=5, max_scan_bytes=4096)
        assert cursor == complete, "the half-written record is left for the live tail"
        assert truncated is True and len(frames) == 5
        assert b'"i": 299' in frames[-1]

    def test_a_line_cut_by_the_chunk_boundary_is_delivered_whole(self, tmp_path):
        from corvin_console.routes import streams
        p = tmp_path / "c.jsonl"
        _append(p, [{"ts": 1.0, "event_type": "a.b", "i": i, "pad": "x" * 40} for i in range(50)])
        cursor, got = 0, []
        for _ in range(500):
            frames, new_cursor = streams._read_new(p, cursor, severity=None, prefixes=None,
                                                   max_bytes=150)
            got.extend(frames)
            if new_cursor == cursor:
                break
            cursor = new_cursor
        ids = [json.loads(f.split(b"data: ", 1)[1])["event"]["i"] for f in got]
        assert ids == list(range(50)), "every record exactly once, none dropped at a cut"
        assert cursor == p.stat().st_size

    def test_a_half_written_tail_is_read_once_it_is_complete(self, tmp_path):
        from corvin_console.routes import streams
        p = tmp_path / "c.jsonl"
        p.write_bytes(b'{"ts": 1, "event_type": "a.b", "i": 1}\n{"ts": 2, "event_type": "a.b", "i"')
        frames, cursor = streams._read_new(p, 0, severity=None, prefixes=None)
        assert len(frames) == 1
        with p.open("ab") as fh:
            fh.write(b': 2}\n')
        frames, cursor = streams._read_new(p, cursor, severity=None, prefixes=None)
        assert len(frames) == 1 and b'"i": 2' in frames[0] and cursor == p.stat().st_size

    def test_the_route_reads_the_chain_on_a_worker_thread(self, home, monkeypatch):
        from corvin_console.routes import streams
        _append(_chain(home), [{"ts": time.time(), "event_type": "console.x", "severity": "INFO"}])
        threads: list[str] = []
        real_backfill, real_new = streams._backfill, streams._read_new

        def spy_backfill(*a, **kw):
            threads.append(threading.current_thread().name)
            return real_backfill(*a, **kw)

        def spy_new(*a, **kw):
            threads.append(threading.current_thread().name)
            return real_new(*a, **kw)

        monkeypatch.setattr(streams, "_backfill", spy_backfill)
        monkeypatch.setattr(streams, "_read_new", spy_new)
        r = _client(streams.router).get("/audit/stream?max_seconds=0.4&poll_interval_s=0.1")
        assert r.status_code == 200
        assert "event: audit.event" in r.text and "event: audit.checkpoint" in r.text
        assert '"history_truncated": false' in r.text
        assert len(threads) >= 2 and "MainThread" not in threads
        main = threading.main_thread().name
        assert all(t != main for t in threads)


# ───────────────────────── R3-NEW-11: maturity dashboard ─────────────────────


class TestMaturityIndex:
    @pytest.fixture
    def ml(self, home, monkeypatch):
        from corvin_console.routes import maturity_live as mod
        monkeypatch.setattr(mod, "_INDEXES", {})
        monkeypatch.setattr(mod, "_audit_chain_path", lambda tid: _chain(home, tid))
        monkeypatch.setattr(mod, "_learning_metrics", lambda tid, since: {
            "outcomes_total": 0, "outcomes_success": 0, "success_rate": None,
            "counts": {}, "available": False})
        return mod

    def test_a_second_request_reads_only_appended_bytes(self, ml, home, monkeypatch):
        chain = _chain(home)
        now = time.time()
        _append(chain, [{"ts": now, "event_type": "os_turn.started"} for _ in range(10)])
        first = ml.build_measurement("_default", "7d")
        assert first["signals"]["audit_events_in_window"] == 10
        assert first["system"]["audit_chain_length"] == 10
        offset = ml._INDEXES["_default"].cursors[str(chain)][1]
        assert offset == chain.stat().st_size

        seeks: list[int] = []
        real_fold = ml._fold_file

        def spy(idx, file_path, **kw):
            seeks.append(idx.cursors.get(str(file_path), (0, 0))[1])
            return real_fold(idx, file_path, **kw)

        monkeypatch.setattr(ml, "_fold_file", spy)
        _append(chain, [{"ts": now, "event_type": "skill.executed"} for _ in range(3)])
        second = ml.build_measurement("_default", "7d")
        assert seeks == [offset], "one pass, starting at the previous end - never at 0"
        assert second["signals"]["audit_events_in_window"] == 13
        assert second["system"]["audit_chain_length"] == 13

    def test_one_measurement_is_one_chain_pass(self, ml, home, monkeypatch):
        """It used to scan the whole chain twice per measurement and four
        times per anomaly request."""
        _append(_chain(home), [{"ts": time.time(), "event_type": "os_turn.started"}])
        passes: list[str] = []
        real = ml._refresh_index
        monkeypatch.setattr(ml, "_refresh_index", lambda t: (passes.append(t), real(t))[1])
        ml.build_measurement("_default", "7d")
        assert len(passes) == 1

    def test_old_events_are_outside_the_window_and_buckets_are_per_tenant(self, ml, home):
        now = time.time()
        _append(_chain(home, "_default"), [
            {"ts": now - 40 * 86400, "event_type": "os_turn.started"},
            {"ts": now, "event_type": "os_turn.started"},
        ])
        _append(_chain(home, "other"), [{"ts": now, "event_type": "os_turn.started"}] * 5)
        assert ml.build_measurement("_default", "7d")["signals"]["audit_events_in_window"] == 1
        assert ml.build_measurement("_default", "90d")["signals"]["audit_events_in_window"] == 2
        assert ml.build_measurement("other", "7d")["signals"]["audit_events_in_window"] == 5
        events, total = ml.read_audit_events("_default", None)
        assert total == 2 and len(events) == 2

    def test_a_replaced_chain_rebuilds_the_index(self, ml, home):
        chain = _chain(home)
        _append(chain, [{"ts": time.time(), "event_type": "a.b"}] * 6)
        assert ml.read_audit_events("_default", None)[1] == 6
        chain.unlink()
        _append(chain, [{"ts": time.time(), "event_type": "a.b"}] * 2)
        assert ml.read_audit_events("_default", None)[1] == 2

    def test_a_half_written_record_is_not_consumed(self, ml, home):
        chain = _chain(home)
        _append(chain, [{"ts": time.time(), "event_type": "a.b"}])
        with chain.open("a") as fh:
            fh.write('{"ts": 1, "event_type": "a.c"')
        assert ml.read_audit_events("_default", None)[1] == 1
        with chain.open("a") as fh:
            fh.write("}\n")
        events, total = ml.read_audit_events("_default", None)
        assert total == 2 and sorted(k for _t, k in events) == ["a.b", "a.c"]

    def test_the_routes_build_on_a_worker_thread_for_the_session_tenant(self, ml, home, monkeypatch):
        from corvin_console.routes import api_vibe_maturity as m1
        from corvin_console.routes import api_vibe_maturity_phase3 as m3
        seen: list[tuple[str, str, str]] = []
        for name in ("build_measurement", "build_history", "build_anomalies", "build_patterns"):
            real = getattr(ml, name)

            def spy(*a, _real=real, _name=name, **kw):
                seen.append((_name, kw.get("tenant_id", ""), threading.current_thread().name))
                return _real(*a, **kw)

            monkeypatch.setattr(ml, name, spy)
        _append(_chain(home, "acme"), [{"ts": time.time(), "event_type": "os_turn.started"}])
        c1, c3 = _client(m1.router, tenant="acme"), _client(m3.router, tenant="acme")
        assert c1.get("/vibe/maturity/measurements?window=7d").status_code == 200
        assert c3.get("/vibe/maturity/historical?loop=context&window=7d").status_code == 200
        assert c3.get("/vibe/maturity/anomalies").status_code == 200
        assert c3.get("/vibe/maturity/patterns").status_code == 200
        main = threading.main_thread().name
        top = [s for s in seen if s[1]]
        assert {s[0] for s in top} == {"build_measurement", "build_history", "build_anomalies",
                                       "build_patterns"}
        assert all(t == "acme" for _n, t, _th in top)
        assert all(th != main for _n, _t, th in top)


# ───────────────────────── R3-NEW-24: dashboard / dual track ─────────────────


class TestDashboardAndDualTrack:
    def test_today_counts_use_a_cursor_not_a_rescan(self, home, monkeypatch):
        from corvin_console.routes import dashboard as mod
        monkeypatch.setattr(mod, "_TODAY_STATE", {})
        chain = _chain(home)
        now = time.time()
        _append(chain, [{"ts": now - 3 * 86400, "severity": "INFO"},
                        {"ts": now, "severity": "INFO"}, {"ts": now, "severity": "WARNING"}])
        counts, window = mod._today_scan("_default")
        assert counts == {"INFO": 1, "WARNING": 1} and window["truncated"] is False
        assert mod._TODAY_STATE["_default"]["offset"] == chain.stat().st_size
        _append(chain, [{"ts": now, "severity": "CRITICAL"}])
        before = mod._TODAY_STATE["_default"]["offset"]
        counts, _w = mod._today_scan("_default")
        assert counts == {"INFO": 1, "WARNING": 1, "CRITICAL": 1}
        assert before < mod._TODAY_STATE["_default"]["offset"] == chain.stat().st_size

    def test_a_window_that_starts_inside_today_says_truncated(self, home, monkeypatch):
        from corvin_console.routes import dashboard as mod
        monkeypatch.setattr(mod, "_TODAY_STATE", {})
        monkeypatch.setattr(mod, "_TODAY_MAX_SCAN_BYTES", 400)
        _append(_chain(home), [{"ts": time.time(), "severity": "INFO", "pad": "x" * 60}
                               for _ in range(50)])
        counts, window = mod._today_scan("_default")
        assert window["truncated"] is True
        assert 0 < counts["INFO"] < 50

    def test_the_dashboard_payload_carries_the_window(self, home, monkeypatch):
        from corvin_console.routes import dashboard as mod
        monkeypatch.setattr(mod, "_TODAY_STATE", {})
        _append(_chain(home), [{"ts": time.time(), "severity": "INFO"}])
        body = _client(mod.router).get("/dashboard").json()
        assert body["today_counts"] == {"INFO": 1}
        assert body["today_counts_window"]["truncated"] is False

    def test_dual_track_reads_a_bounded_tail_and_reports_it(self, home, monkeypatch):
        from corvin_console.routes import chain_dual_track as mod
        monkeypatch.setattr(mod, "_verify_all_chains", lambda tid: True)
        chain = _chain(home)
        _append(chain, [{"ts": float(i), "event_type": "os_turn.started",
                         "details": {"chat_key": "s1", "pad": "x" * 50}} for i in range(200)])
        monkeypatch.setattr(mod, "_MAX_SCAN_BYTES", 2048)
        out = mod.get_chain_dual_track(sid="web:s1", rec=_fake_record())
        assert out["window"]["truncated"] is True
        assert out["window"]["scanned_bytes"] < out["window"]["total_bytes"]
        assert 0 < len(out["os_only_events"]) < 200
        monkeypatch.setattr(mod, "_MAX_SCAN_BYTES", 32 * 1024 * 1024)
        out = mod.get_chain_dual_track(sid="web:s1", rec=_fake_record())
        assert out["window"]["truncated"] is False and len(out["os_only_events"]) == 200


# ───────────────────────── R3-NEW-25: multi-instance ─────────────────────────


class TestMultiInstance:
    def test_no_handler_runs_on_the_event_loop(self):
        from corvin_console.routes import multi_instance as mod
        names = [r.name for r in mod.router.routes]
        assert "trigger_manual_sync" in names
        for r in mod.router.routes:
            assert not asyncio.iscoroutinefunction(r.endpoint), r.name

    def test_vault_items_are_bound_to_the_tenant(self, monkeypatch):
        from corvin_console.routes import multi_instance as mod
        asked: list[str] = []
        fake = type(sys)("vault")
        fake.get_item = lambda name, source="?": (asked.append(name), "secret-of-" + name)[1]
        monkeypatch.setitem(sys.modules, "vault", fake)
        assert mod._vault_item("cross_device_sync_pat", "_default") == "secret-of-cross_device_sync_pat"
        mod._vault_item("cross_device_sync_pat", "acme")
        assert asked == ["cross_device_sync_pat", "cross_device_sync_pat__acme"]
        long_tenant = "t" * 60
        name = mod._vault_item_name("cross_device_sync_passphrase", long_tenant)
        assert len(name) <= 40 and name != "cross_device_sync_passphrase"
        assert name != mod._vault_item_name("cross_device_sync_passphrase", "u" * 60)


# ───────────────────────── R3-NEW-26: override authority ─────────────────────


class _MemoryChain:
    def __init__(self):
        self.events: list[dict] = []
        self.threads: list[str] = []

    def write_event_dict(self, **kw):
        self.threads.append(threading.current_thread().name)
        self.events.append(kw)


class TestOverrides:
    @pytest.fixture
    def ov(self, home, monkeypatch):
        from corvin_console.routes import control_plane_overrides as mod
        from core.control_plane.override_authority import OverrideAuthority
        chain = _MemoryChain()
        authority = OverrideAuthority(tenant_id="_default", audit_backend=chain)
        monkeypatch.setattr(mod, "_authority", authority)
        calls = _silence_audit(monkeypatch, mod)
        app = FastAPI()
        app.include_router(mod.router)
        rec = _fake_record()
        for dep in (console_deps.require_session, console_deps.require_csrf,
                    console_deps.require_session_csrf_on_mutation):
            app.dependency_overrides[dep] = lambda rec=rec: rec
        # The consent gate is real: grant the scope to this session's subject.
        from core.compliance import consent_store
        from core.compliance.consent import consent_subject
        consent_store._stores.clear()
        consent_store.get_consent_store("_default", corvin_home=home).grant_consent(
            user_id=consent_subject(rec), scope="control_plane_override_operations")
        return mod, authority, chain, calls, TestClient(app, raise_server_exceptions=False), home

    def _request(self, client):
        r = client.post("/control-plane/overrides", json={
            "override_type": "force_enable", "target_id": "plugin-x", "reason": "maintenance"})
        assert r.status_code == 200, r.text
        return r.json()["override_id"]

    def test_decisions_are_501_while_no_approver_exists(self, ov):
        _mod, authority, _chain_, _calls, client, _home = ov
        oid = self._request(client)
        for verb in ("approve", "deny"):
            r = client.post(f"/control-plane/overrides/{oid}/{verb}", json={"reason": "x"})
            assert r.status_code == 501, r.text
            assert r.json()["detail"]["available"] is False
            assert r.json()["detail"]["error"] == "approvals_not_available"
        listing = client.get("/control-plane/overrides").json()
        assert listing["approvals_available"] is False and listing["count"] == 1
        authority.add_approver("sid:someone-else")
        r = client.post(f"/control-plane/overrides/{oid}/approve", json={"reason": "x"})
        assert r.status_code == 403, "with approvers configured it is a permission question again"
        assert client.get("/control-plane/overrides").json()["approvals_available"] is True

    def test_chain_writes_leave_the_event_loop(self, ov):
        _mod, _authority, chain, calls, client, _home = ov
        self._request(client)
        main = threading.main_thread().name
        assert chain.events and chain.events[0]["event_type"] == "override_requested"
        assert all(t != main for t in chain.threads)
        assert calls and all(kw["_thread"] != main for _n, kw in calls)

    def test_audit_log_counts_corrupt_lines_instead_of_stopping(self, ov):
        _mod, _authority, _chain_, _calls, client, home = ov
        chain = _chain(home)
        with chain.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_type": "override_requested", "tenant_id": "_default",
                                 "details": {"override_id": "o1"}}) + "\n")
            fh.write('{"event_type": "override_approved", "details": {"overri\n')   # corrupt
            fh.write(json.dumps({"event_type": "override_rejected", "tenant_id": "_default",
                                 "details": {"override_id": "o2"}}) + "\n")
            fh.write(json.dumps({"event_type": "override_requested", "tenant_id": "other",
                                 "details": {"override_id": "o3"}}) + "\n")
        body = client.get("/control-plane/overrides/audit").json()
        assert [e["details"]["override_id"] for e in body["events"]] == ["o1", "o2"], \
            "the record AFTER the corrupt line is still returned; the other tenant's is not"
        assert body["corrupt_lines"] == 1 and body["complete"] is False
        assert body["window"]["truncated"] is False

    def test_audit_log_window_is_bounded_and_reported(self, home):
        from core.control_plane.override_authority import OverrideAuthority
        chain = _chain(home)
        _append(chain, [{"event_type": "override_requested", "tenant_id": "_default",
                         "details": {"override_id": f"o{i}"}} for i in range(100)])
        out = OverrideAuthority(audit_backend=_MemoryChain()).read_audit_log(
            "_default", max_scan_bytes=1000)
        assert out["window"]["truncated"] is True and out["complete"] is False
        assert 0 < len(out["events"]) < 100 and out["events"][-1]["details"]["override_id"] == "o99"


@pytest.mark.parametrize("rel", ("control_plane_overrides.py", "rag_hub.py", "voice_summary.py",
                                 "files.py", "webhooks.py"))
def test_async_handlers_write_the_chain_only_through_to_thread(rel):
    """A direct ``console_audit.*`` / ``_security_events.*`` call in an async
    handler is a chain write (lock + fsync) on the event loop."""
    tree = ast.parse((_ROUTES / rel).read_text(encoding="utf-8"))
    offenders, threaded = [], 0
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)):
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Call):
                f = node.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id in ("console_audit", "_security_events")):
                    offenders.append(f"{rel}:{node.lineno} {fn.name} -> {f.value.id}.{f.attr}()")
                if isinstance(f, ast.Attribute) and f.attr == "to_thread":
                    threaded += 1
            stack.extend(ast.iter_child_nodes(node))
    assert offenders == []
    assert threaded > 0, "positive control: the module does hand work to to_thread"


# ───────────────────────── R3-NEW-17 / 18: layer forge ───────────────────────


class TestLayerForge:
    @pytest.fixture
    def orch(self, home, monkeypatch):
        th = home / "tenants" / "_default"
        for sub in ("global/layer_forge/registry", "global/layer_forge/locks"):
            (th / sub).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
        from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator
        from core.orchestration.layer_forge.primitive import atomic_write_json
        o = LayerForgeOrchestrator("_default")
        manifest = {"id": "test.flagged.layer", "version": "1.0.0", "targets": [{"layer_id": "L34"}],
                    "quality_gates": [{"gate_id": "schema_check",
                                       "test_path": "tests/layer_forge/test_schema.py"}],
                    "enforcement_rules": []}
        o.registry.validate(manifest)
        record = dict(manifest, status="accepted", review_flagged=True,
                      review_flags=["scope_creep"])
        atomic_write_json(o.registry._path_for("test.flagged.layer", "1.0.0"), record)
        return o, home

    def _events(self, home, name):
        chain = _chain(home)
        if not chain.exists():
            return []
        return [r for r in map(json.loads, filter(None, chain.read_text().splitlines()))
                if r.get("event_type") == name]

    def test_a_refused_transition_records_no_override(self, orch, monkeypatch):
        from core.orchestration.layer_forge import learning_integration as li
        from core.orchestration.layer_forge.registry import LayerPromotionError
        o, home = orch
        learned: list[str] = []
        monkeypatch.setattr(li, "emit_override_outcome", lambda **kw: learned.append("x") or True)
        with pytest.raises(LayerPromotionError):
            o.promote("test.flagged.layer", "1.0.0", "accepted",   # accepted -> accepted: invalid
                      override_review_flags=True, override_reason="looks fine")
        assert self._events(home, "layer_forge.review_override_applied") == []
        assert learned == [], "no learning signal for an override that did not happen"
        assert o.get("test.flagged.layer", "1.0.0")["status"] == "accepted"

    def test_an_override_needs_a_reason(self, orch):
        from core.orchestration.layer_forge.registry import LayerPromotionError
        o, home = orch
        for reason in ("", "   "):
            with pytest.raises(LayerPromotionError, match="needs a reason"):
                o.promote("test.flagged.layer", "1.0.0", "deployed",
                          override_review_flags=True, override_reason=reason)
        assert self._events(home, "layer_forge.review_override_applied") == []

    def test_the_reason_reaches_the_chain_as_a_fingerprint_only(self, orch, monkeypatch):
        from core.orchestration.layer_forge import learning_integration as li
        o, home = orch
        learned: list[dict] = []
        monkeypatch.setattr(li, "emit_override_outcome", lambda **kw: learned.append(kw) or True)
        reason = "Customer Jane Doe confirmed by phone that the scope is fine " * 20
        out = o.promote("test.flagged.layer", "1.0.0", "deployed",
                        override_review_flags=True, override_reason=reason)
        assert out["status"] == "deployed"
        events = self._events(home, "layer_forge.review_override_applied")
        assert len(events) == 1
        raw = json.dumps(events[0])
        assert "Jane Doe" not in raw and "confirmed by phone" not in raw
        recorded = (events[0].get("details") or events[0])["override_reason"]
        digest = hashlib.sha256(reason.strip().encode()).hexdigest()[:16]
        assert recorded == f"sha256:{digest};len:{len(reason.strip())}"
        assert len(learned) == 1
        order = [r["event_type"] for r in map(json.loads, _chain(home).read_text().splitlines())
                 if r["event_type"].startswith("layer_forge.")]
        assert order == ["layer_forge.review_override_applied", "layer_forge.definition_transitioned"]

    def test_mutating_routes_need_an_owner(self, home):
        from corvin_console.routes import layer_forge as mod
        client = _client(mod.router, tier="operator")
        assert client.post("/layer-forge/definitions", json={"id": "x"}).status_code == 403
        assert client.post("/layer-forge/plan", json={"layer_id": "L1", "intent": "x"}).status_code == 403
        assert client.post("/layer-forge/definitions/a.b/1.0.0/transition",
                           json={"to_status": "deployed"}).status_code == 403
        assert client.post("/layer-forge/gate-thresholds/analyze",
                           json={"gate_id": "g"}).status_code == 403
        assert client.post("/layer-forge/gate-thresholds/apply",
                           json={"gate_id": "g", "new_threshold": 1, "reason": "r"}).status_code == 403
        assert client.get("/layer-forge/definitions").status_code == 200, "reads stay open"

    def test_the_route_refuses_an_override_without_a_reason(self, home):
        from corvin_console.routes import layer_forge as mod
        client = _client(mod.router)
        r = client.post("/layer-forge/definitions/a.b/1.0.0/transition",
                        json={"to_status": "deployed", "override_review_flags": True})
        assert r.status_code == 422, r.text
        r = client.post("/layer-forge/definitions/a.b/1.0.0/transition",
                        json={"to_status": "deployed", "override_review_flags": True,
                              "override_reason": "x" * 501})
        assert r.status_code == 422, "the reason is bounded"


# ───────────────────────── R3-NEW-20: file upload ────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
class TestFileUpload:
    @pytest.fixture
    def fx(self, home, monkeypatch):
        from corvin_console.routes import files as mod
        calls = _silence_audit(monkeypatch, mod)
        root = Path(mod._forge_paths.tenant_home("_default"))
        (root / "files").mkdir(parents=True, exist_ok=True)
        return mod, calls, root, _client(mod.router)

    def _upload(self, client, mod, name, data=b"OVERWRITTEN-BY-UPLOAD", dir_="files"):
        path = next(r.path for r in mod.router.routes if getattr(r, "name", "") == "files_upload")
        return client.post(f"{path}?dir={dir_}", files={"file": (name, data)})

    def test_an_upload_does_not_write_through_a_link_that_leaves_the_tenant(self, fx, tmp_path):
        mod, calls, root, client = fx
        outside = tmp_path / "outside_target.txt"
        outside.write_text("ORIGINAL")
        os.symlink(outside, root / "files" / "link.txt")
        r = self._upload(client, mod, "link.txt")
        assert r.status_code == 403, r.text
        assert outside.read_text() == "ORIGINAL"
        assert (root / "files" / "link.txt").is_symlink()
        assert any(n == "action_denied" for n, _kw in calls)

    def test_an_upload_does_not_write_through_a_link_inside_the_tenant(self, fx):
        mod, _calls, root, client = fx
        victim = root / "files" / "victim.txt"
        victim.write_text("ORIGINAL")
        os.symlink(victim, root / "files" / "alias.txt")
        assert self._upload(client, mod, "alias.txt").status_code == 403
        assert victim.read_text() == "ORIGINAL"

    def test_escape_counts_as_write_protected(self, fx, tmp_path):
        mod, _calls, root, _client_ = fx
        os.symlink(tmp_path, root / "files" / "out")
        assert mod._write_protected_class(root, "files/out/x.txt") == mod._OUTSIDE_ROOT
        assert mod._write_protected_class(root, "files/ok.txt") is None

    def test_a_plain_upload_still_works_and_replaces_atomically(self, fx):
        mod, _calls, root, client = fx
        assert self._upload(client, mod, "a.txt", b"one").status_code == 200
        r = self._upload(client, mod, "a.txt", b"two-two")
        assert r.status_code == 200 and r.json()["size_bytes"] == 7
        assert (root / "files" / "a.txt").read_bytes() == b"two-two"
        assert [p.name for p in (root / "files").iterdir()] == ["a.txt"], "no temp file is left"

    def test_an_aborted_upload_leaves_nothing_behind(self, fx, monkeypatch):
        mod, _calls, root, client = fx
        monkeypatch.setattr(mod, "_MAX_UPLOAD_BYTES", 10)
        assert self._upload(client, mod, "big.bin", b"x" * 100).status_code == 413
        assert list((root / "files").iterdir()) == []


# ───────────────────────── R3-NEW-21: webhook rate limits ────────────────────


class TestWebhookBuckets:
    SECRET = "whsec-test"

    @pytest.fixture
    def wh(self, home, monkeypatch):
        from corvin_console.routes import webhooks as mod
        monkeypatch.setattr(mod, "_rate_hits", {})
        monkeypatch.setattr(mod, "_unauth_hits", {})
        monkeypatch.setattr(mod, "_seen_signatures", {})
        monkeypatch.setattr(mod, "_find_channel", lambda tid, cid: {
            "channel_id": cid, "hmac_secret_env": "CORVIN_WH_TEST", "rate_limit_per_hour": 3})
        monkeypatch.setattr(mod, "_audit_chain_path", lambda tid: _chain(home, tid))
        written: list[str] = []
        monkeypatch.setattr(mod._security_events, "write_event",
                            lambda path, name, **kw: written.append(
                                name + "@" + threading.current_thread().name))
        monkeypatch.setenv("CORVIN_WH_TEST", self.SECRET)
        addr = {"v": "203.0.113.9"}
        monkeypatch.setattr(mod, "_client_addr", lambda request: addr["v"])
        return mod, _client(mod.router), addr, written

    def _sig(self, body: bytes) -> dict:
        return {"X-Hub-Signature-256": "sha256=" + hmac.new(
            self.SECRET.encode(), body, hashlib.sha256).hexdigest()}

    def test_unsigned_posts_do_not_lock_the_legitimate_sender_out(self, wh):
        mod, client, addr, _written = wh
        url = "/webhook/_default/chan"
        codes = [client.post(url, content=b"{}").status_code for _ in range(6)]
        assert codes == [401, 401, 401, 429, 429, 429], codes
        assert mod._rate_hits == {}, "failed attempts never spend the channel's budget"
        addr["v"] = "198.51.100.7"                       # the real sender
        r = client.post(url, content=b'{"n": 1}', headers=self._sig(b'{"n": 1}'))
        assert r.status_code == 202, r.text

    def test_only_signed_deliveries_spend_the_channel_limit(self, wh):
        _mod, client, _addr, _written = wh
        url = "/webhook/_default/chan"
        codes = []
        for i in range(5):
            body = json.dumps({"n": i}).encode()
            codes.append(client.post(url, content=body, headers=self._sig(body)).status_code)
        assert codes == [202, 202, 202, 429, 429], codes

    def test_the_answer_says_accepted_not_delivered(self, wh):
        _mod, client, _addr, written = wh
        body = b'{"hello": "world"}'
        r = client.post("/webhook/_default/chan", content=body, headers=self._sig(body))
        assert r.status_code == 202
        out = r.json()
        assert out["accepted"] is True and out["delivered"] is False and out["recorded"] is True
        assert out["delivery"] == "not_available"
        main = threading.main_thread().name
        assert [w.split("@")[0] for w in written] == ["webhook.message_received"]
        assert all(w.split("@")[1] != main for w in written)


# ───────────────────────── R3-NEW-22: archive intake ─────────────────────────


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


_GOOD_MANIFEST = json.dumps({"name": "p", "version": "1.0.0", "author": "t"}).encode()


class TestArchiveIntake:
    @pytest.fixture
    def staging(self, home):
        from core.plugins import staging as mod
        return mod, mod.StagingManager("_default")

    def test_only_the_package_manifest_counts(self, staging):
        mod, mgr = staging
        assert mod.find_manifest_entry(["manifest.json", "a/manifest.json"]) == "manifest.json"
        assert mod.find_manifest_entry(["pkg/manifest.json", "pkg/x.py"]) == "pkg/manifest.json"
        assert mod.find_manifest_entry(["docs/old-manifest.json", "x.py"]) is None
        assert mod.find_manifest_entry(["a/b/manifest.json"]) is None
        assert mod.find_manifest_entry(["a/manifest.json", "b/x.py"]) is None
        ok, _m, errors = mgr.validate_zip_file(io.BytesIO(_zip({"xmanifest.json": _GOOD_MANIFEST})))
        assert ok is False and "manifest.json not found" in errors[0]
        ok, manifest, errors = mgr.validate_zip_file(io.BytesIO(_zip({"manifest.json": _GOOD_MANIFEST})))
        assert ok is True and manifest["name"] == "p", errors

    @pytest.mark.parametrize("entries", [
        {"manifest.json": _GOOD_MANIFEST, "src\\evil.py": b"x"},
        {"manifest.json": _GOOD_MANIFEST, "A.py": b"1", "a.py": b"2"},
        {"manifest.json": _GOOD_MANIFEST, "../escape.py": b"x"},
    ])
    def test_staging_reuses_the_installer_entry_checks(self, staging, entries):
        _mod, mgr = staging
        ok, _m, errors = mgr.validate_zip_file(io.BytesIO(_zip(entries)))
        assert ok is False and errors[0].startswith("Disallowed archive"), errors

    def test_entry_count_and_unpacked_size_are_capped(self, staging, tmp_path):
        mod, _mgr = staging
        many = _zip({"manifest.json": _GOOD_MANIFEST, **{f"f{i}.txt": b"" for i in range(30)}})
        with zipfile.ZipFile(io.BytesIO(many)) as zf, pytest.raises(mod.StagingError, match="entries"):
            mod.check_zip_archive(zf, tmp_path / "t", max_entries=10)
        bomb = _zip({"manifest.json": _GOOD_MANIFEST, "zeros.bin": b"\0" * (2 * 1024 * 1024)})
        assert len(bomb) < 20_000, "positive control: it really is a compression bomb"
        with zipfile.ZipFile(io.BytesIO(bomb)) as zf, pytest.raises(mod.StagingError, match="unpacks"):
            mod.check_zip_archive(zf, tmp_path / "t", max_unpacked_bytes=1024 * 1024)

    def test_the_upload_route_refuses_a_bomb_and_extracts_nothing(self, home, monkeypatch):
        from core.plugins import staging
        from corvin_console.routes import packages as mod
        monkeypatch.setattr(staging, "MAX_ZIP_UNPACKED_BYTES", 1024 * 1024)
        monkeypatch.setattr(staging.check_zip_archive, "__kwdefaults__", {
            "max_entries": staging.MAX_ZIP_ENTRIES, "max_unpacked_bytes": 1024 * 1024})
        bomb = _zip({"manifest.json": json.dumps({"name": "bomb", "version": "1.0.0"}).encode(),
                     "zeros.bin": b"\0" * (4 * 1024 * 1024)})
        tmp_root = mod._registry_root("_default") / "temp"
        client = _client(mod.router, extra_overrides=(mod.require_feature_csrf, mod.require_feature))
        path = next(r.path for r in mod.router.routes if getattr(r, "name", "") == "upload_package")
        r = client.post(path, files={"file": ("p.zip", bomb, "application/zip")})
        assert r.status_code == 400, r.text
        assert not tmp_root.exists() or list(tmp_root.iterdir()) == []

    def test_the_upload_route_reads_a_bounded_body(self, home, monkeypatch):
        from corvin_console.routes import packages as mod
        monkeypatch.setattr(mod, "_MAX_PACKAGE_BYTES", 1000)
        client = _client(mod.router, extra_overrides=(mod.require_feature_csrf, mod.require_feature))
        path = next(r.path for r in mod.router.routes if getattr(r, "name", "") == "upload_package")
        r = client.post(path, files={"file": ("p.zip", b"x" * 5000, "application/zip")})
        assert r.status_code == 413, r.text
        src = (_ROUTES / "packages.py").read_text(encoding="utf-8")
        assert "= await file.read()" not in src and "await file.read(_MAX_PACKAGE_BYTES + 1)" in src


# ───────────────────────── R3-NEW-30: voice health ───────────────────────────


class TestVoiceHealth:
    @pytest.fixture
    def vs(self, monkeypatch):
        from corvin_console.routes import voice_summary as mod
        from corvin_console.services import stt_engine
        monkeypatch.setattr(stt_engine, "_availability", None)
        monkeypatch.setattr(mod, "_voice_sessions", {})
        _silence_audit(monkeypatch, mod)
        return mod, stt_engine, _client(mod.router)

    def test_health_is_degraded_when_no_real_provider_is_ready(self, vs, monkeypatch):
        mod, stt_engine, client = vs
        monkeypatch.setattr(stt_engine, "_probe_platform_stt", lambda: False)
        body = client.get("/chat/voice/health").json()
        assert body["status"] == "degraded" and body["stt_available"] is False
        assert body["summary_available"] is False
        r = client.post("/chat/s1/voice/start", json={})
        assert r.status_code == 503 and mod._voice_sessions == {}

    def test_health_follows_the_real_provider_status(self, vs, monkeypatch):
        _mod, stt_engine, client = vs
        fake = type(sys)("stt")
        fake.provider_status = lambda: {"local": {"ready": False}, "openai": {"ready": True}}
        monkeypatch.setitem(sys.modules, "stt", fake)
        body = client.get("/chat/voice/health").json()
        assert body["status"] == "healthy" and body["stt_available"] is True
        assert "not generated" in body["message"], "it does not claim a summary feature"
        fake.provider_status = lambda: {"local": {"ready": False}}
        monkeypatch.setattr(stt_engine, "_availability", None)
        assert client.get("/chat/voice/health").json()["status"] == "degraded"

    def test_the_mock_is_never_the_default(self, vs, monkeypatch):
        _mod, stt_engine, _client_ = vs
        monkeypatch.setattr(stt_engine, "_stt_engine", None)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        engine = stt_engine.get_stt_engine()
        assert engine.provider is stt_engine.STTProvider.GOOGLE_CLOUD and engine.available is False
        result, error = asyncio.run(engine.transcribe(b"audio"))
        assert result is None and "unavailable" in error
        with pytest.raises(TypeError):
            stt_engine.STTEngine()  # a provider must be named

    def test_the_session_store_is_bounded(self, vs, monkeypatch):
        mod, stt_engine, client = vs
        monkeypatch.setattr(stt_engine, "_probe_platform_stt", lambda: True)
        monkeypatch.setattr(mod, "_MAX_VOICE_SESSIONS", 5)
        for i in range(12):
            assert client.post(f"/chat/s{i}/voice/start", json={}).status_code == 200
        assert len(mod._voice_sessions) == 5
        assert [k[1] for k in mod._voice_sessions] == [f"s{i}" for i in range(7, 12)]
        stale = next(iter(mod._voice_sessions.values()))
        stale["_created_ts"] = time.time() - mod._VOICE_SESSION_TTL_S - 1
        assert client.post("/chat/new/voice/start", json={}).status_code == 200
        assert ("_default", "s7") not in mod._voice_sessions
        r = client.post("/chat/new/voice/stop", json={"transcript": "x" * (mod._MAX_TRANSCRIPT_CHARS + 1)})
        assert r.status_code == 422
