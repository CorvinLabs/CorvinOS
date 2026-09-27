"""LIVE: one real console chat turn carries the L5 shadow-router record AND a
priced engine span in the SAME tenant chain.

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH (one short haiku turn).

Difference to the existing live tests:

* ``tests/skills/test_delegation_router_live_llm.py`` calls
  ``resolve_worker_engine`` directly and then runs an UNRELATED ``claude -p`` —
  the shadow record and the LLM turn are never the same turn;
* ``core/console/tests/test_chat_live_llm_e2e.py`` /
  ``test_learning_loop_live_e2e.py`` boot the ACP registry with a NO-OP
  ``audit_emit`` (``lambda *_a, **_k: None``), so the router's
  ``skill.executed`` never reaches the hash chain in those runs.

Here the registry is booted exactly as ``bootstrap.boot_platform`` boots it —
``boot_skills(tenant, audit_emit=_default_audit_emit(tenant))``, the real
chained writer — and the turn goes through the real console router over HTTP
(session create, CSRF) and the real chat WebSocket. The chain read back is the
one ``tenant_audit_chain()`` resolves, the file ``model_usage`` and the
compliance reports read. Asserted:

1. the turn answers (``done``, no ``error``, reply contains the token);
2. ``skill.executed`` for ``os.delegation_router`` is in the tenant chain,
   shadow, bundled engine ``native``, LoM = the shadow call site — and it
   precedes the turn's ``engine.span.start`` (routing decided before spawn);
3. the turn's ``engine.span.end`` (role ``os``) carries the pinned registry
   model id, and ``model_usage`` counts that turn with real tokens;
4. no prompt text in the chain.

Run: CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
     -p no:cacheprovider tests/e2e/live/test_console_turn_shadow_router_live.py -s
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONSOLE_TESTS = REPO / "core" / "console" / "tests"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
        reason="live console E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
    ),
]

HAIKU = "claude-haiku-4-5-20251001"
PROMPT = "Reply with exactly the single word PONG and nothing else."


def test_console_turn_shadow_router_and_priced_span_share_one_chain(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin_home"
    tid = "_default"
    th = home / "tenants" / tid
    for d in ("global/auth", "global/forge", "global/console/sessions"):
        (th / d).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", tid)
    for k in ("FORGE_ROOT", "CORVIN_OS_MODEL_OVERRIDE", "CORVIN_ACP_PHASE"):
        monkeypatch.delenv(k, raising=False)
    for p in (str(CONSOLE_TESTS), str(REPO)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from core.paths import tenant_audit_chain

    chain = Path(tenant_audit_chain(tid))
    assert home in chain.parents, chain
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))

    from test_learning_loop_routes_e2e import _reset_modules  # noqa: PLC0415

    _reset_modules()
    emitter = None
    try:
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from corvin_plugins.bootstrap import _default_audit_emit
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore
        from core.skills.boot import boot_skills
        from core.skills.skill_registry_phase1 import LearningEmitterBackend
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        emitter = EventEmitter(EventStore(th))
        registered = boot_skills(
            tid,
            audit_emit=_default_audit_emit(tid),  # the production boot's writer
            learning_backend=LearningEmitterBackend(emitter, session_id="live"),
        )
        assert "os.delegation_router" in registered, registered

        (th / "global" / "tenant.corvin.yaml").write_text(
            "spec:\n  default_engine: claude_code\n  engine_models:\n"
            f"    claude_code:\n      os_model: {HAIKU}\n",
            encoding="utf-8",
        )
        os.chmod(th / "global" / "tenant.corvin.yaml", 0o600)

        rec = _auth.create_session(tenant_id=tid, token_fingerprint="live-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)})

        r = client.post("/v1/console/chat/sessions", json={"title": "live shadow router"})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        sid = (body.get("session") or {}).get("sid") or body.get("sid") or body.get("session_id")
        assert sid, body

        types: list[str] = []
        texts: list[str] = []
        t0 = time.monotonic()
        with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user", "text": PROMPT})
            while time.monotonic() - t0 < 600:
                msg = ws.receive_json()
                types.append(msg.get("type"))
                if msg.get("type") in ("result", "delta", "text"):
                    texts.append(str(msg.get("text") or msg.get("delta") or ""))
                if msg.get("type") == "done":
                    break
        answer = "".join(texts).strip()
        print(f"\n[live] console turn {time.monotonic() - t0:.1f}s types={types} answer={answer!r}")
        assert "done" in types and "error" not in types, types
        assert "PONG" in answer.upper(), answer

        # the emitter drains asynchronously; the chain writes are synchronous
        events = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        etypes = [e.get("event_type") for e in events]
        print(f"[live] tenant chain: {etypes}")

        routed = [(i, e) for i, e in enumerate(events) if e.get("event_type") == "skill.executed"
                  and (e.get("details") or {}).get("skill_id") == "os.delegation_router"]
        assert routed, "no os.delegation_router skill.executed in the tenant chain for a real turn"
        ri, rrec = routed[0]
        d = rrec["details"]
        assert d.get("lom") == "corvin_operator/bridges/shared/delegation_policy.py:_acp_shadow_route", d
        assert d.get("decision", {}).get("shadow") is True, d
        assert d["decision"].get("bundled_engine") == "native", d

        span_starts = [i for i, e in enumerate(events) if e.get("event_type") == "engine.span.start"
                       and (e.get("details") or {}).get("role") == "os"]
        assert span_starts, f"no OS engine.span.start in the tenant chain: {etypes}"
        assert ri < span_starts[-1], "routing record written after the engine was spawned"
        ends = [e["details"] for e in events if e.get("event_type") == "engine.span.end"
                and (e.get("details") or {}).get("role") == "os"]
        assert ends and ends[-1].get("model_id") == HAIKU, ends

        from corvin_console.model_usage import model_usage  # noqa: PLC0415

        rows = {m["model_id"]: m for m in model_usage(tid)["models"]}
        assert HAIKU in rows and rows[HAIKU]["output_tokens"] > 0, rows
        assert "single word PONG" not in chain.read_text(), "prompt text leaked into the chain"
    finally:
        if emitter is not None:
            try:
                emitter.stop(timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
        _reset_modules()
