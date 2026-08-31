"""ADR-0171 M3 — the /engine-spans endpoint: every engine (OS + worker, any
engine_id, both audit chains) surfaces as one paired, engine-agnostic span.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "core" / "gateway",
           _REPO / "operator" / "forge", _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _reset():
    for n in list(sys.modules):
        if n.startswith("corvin_console") or n in ("forge.paths",):
            sys.modules.pop(n, None)


@contextmanager
def _sandbox(tid: str = "_default"):
    with tempfile.TemporaryDirectory(prefix="espan-ep-") as td:
        home = Path(td) / "corvin"
        (home / "tenants" / tid / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tid / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tid / "global" / "console" / "sessions").mkdir(parents=True)
        (home / "global" / "forge").mkdir(parents=True)
        prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "XDG_CONFIG_HOME")}
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(Path(td) / "xdg")
        (Path(td) / "xdg" / "corvin-voice").mkdir(parents=True)
        try:
            _reset()
            from corvin_console import auth as A
            from corvin_console import chat_runtime
            from corvin_console.app import router
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from forge import security_events as SEC
            rec = A.create_session(tenant_id=tid)
            app = FastAPI(); app.include_router(router, prefix="/v1/console")
            client = TestClient(app); client.cookies.set("corvin_console_sid", rec.sid)
            yield {"client": client, "home": home, "tid": tid,
                   "chat_runtime": chat_runtime, "SEC": SEC}
        finally:
            for k, v in prev.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            _reset()


def test_engine_spans_pairs_os_and_worker_across_chains():
    with _sandbox() as ctx:
        cr, SEC, home, tid = ctx["chat_runtime"], ctx["SEC"], ctx["home"], ctx["tid"]
        sess = cr.create_session(tid, title="t")
        wd = cr._workdir(tid, sess.sid)
        # Worker span (ACS chain) needs its run dir under the session workdir.
        run_id = "acs-test-abc123"
        (wd / "acs" / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        os_chain = home / "global" / "forge" / "audit.jsonl"
        acs_chain = home / "tenants" / tid / "global" / "audit.jsonl"
        # OS span (claude) — carries chat_key.
        for ev, det in [
            ("engine.span.start", {"span_id": "spn-os-1", "role": "os", "engine_id": "claude_code", "model_id": "claude-opus-4-8", "chat_key": sess.chat_key, "started_at": 1.0}),
            ("engine.span.end",   {"span_id": "spn-os-1", "role": "os", "engine_id": "claude_code", "model_id": "claude-opus-4-8", "chat_key": sess.chat_key, "status": "ok", "duration_ms": 900}),
        ]:
            SEC.write_event(os_chain, ev, details=det)
        # Worker span (hermes) — carries run_id, scoped by the run dir.
        for ev, det in [
            ("engine.span.start", {"span_id": "spn-w-1", "role": "worker", "engine_id": "hermes", "model_id": "qwen3:8b", "run_id": run_id, "started_at": 2.0}),
            ("engine.span.end",   {"span_id": "spn-w-1", "role": "worker", "engine_id": "hermes", "model_id": "qwen3:8b", "run_id": run_id, "status": "ok", "duration_ms": 1500, "tokens_used": 222}),
        ]:
            SEC.write_event(acs_chain, ev, details=det)

        r = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/engine-spans")
        assert r.status_code == 200, r.text
        body = r.json()
        by_id = {s["span_id"]: s for s in body["spans"]}
        assert "spn-os-1" in by_id and "spn-w-1" in by_id, body
        assert by_id["spn-os-1"]["role"] == "os" and by_id["spn-os-1"]["completed"] is True
        assert by_id["spn-w-1"]["role"] == "worker" and by_id["spn-w-1"]["tokens_used"] == 222
        # engine-agnostic roll-up: BOTH engines present
        assert set(body["engines"]) == {"claude_code", "hermes"}
        assert set(body["roles"]) == {"os", "worker"}


def test_role_filter():
    with _sandbox() as ctx:
        cr, SEC, home, tid = ctx["chat_runtime"], ctx["SEC"], ctx["home"], ctx["tid"]
        sess = cr.create_session(tid, title="t")
        os_chain = home / "global" / "forge" / "audit.jsonl"
        SEC.write_event(os_chain, "engine.span.start", details={"span_id": "o1", "role": "os", "engine_id": "claude_code", "chat_key": sess.chat_key, "started_at": 1.0})
        SEC.write_event(os_chain, "engine.span.end", details={"span_id": "o1", "role": "os", "engine_id": "claude_code", "chat_key": sess.chat_key, "status": "ok"})
        r = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/engine-spans?role=worker")
        assert r.status_code == 200
        assert r.json()["count"] == 0  # no worker spans → filtered out
        r2 = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/engine-spans?role=os")
        assert r2.json()["count"] == 1


def test_fresh_session_empty_not_500():
    with _sandbox() as ctx:
        sess = ctx["chat_runtime"].create_session(ctx["tid"], title="t")
        r = ctx["client"].get(f"/v1/console/chat/sessions/{sess.sid}/engine-spans")
        assert r.status_code == 200 and r.json()["count"] == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
