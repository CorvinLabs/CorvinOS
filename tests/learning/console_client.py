"""Real-boundary console client for the learning route tests.

Builds the REAL console router (``corvin_console.app.router`` mounted at
``/v1/console``, exactly as ``standalone.create_app`` mounts it) inside a
sandboxed ``CORVIN_HOME`` with an isolated core audit chain, and authenticates
with a REAL console session: ``auth.create_session`` writes the session record
under the sandbox, the client carries its ``corvin_console_sid`` cookie and the
derived CSRF token. Nothing in the auth path is overridden — a test that sends
no cookie gets the real 401, a POST without the CSRF header the real 403, and
the metrics WebSocket authenticates the same cookie ``require_session`` reads.

Mirrors the ``_sandbox`` pattern of
``core/console/tests/test_learning_loop_routes_e2e.py``.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

_REPO = Path(__file__).resolve().parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
for _p in [
    str(_OPERATOR),
    str(_OPERATOR / "license"),
    str(_OPERATOR / "forge"),
    str(_OPERATOR / "bridges" / "shared"),
    str(_CONSOLE),
    str(_REPO),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

COOKIE_NAME = "corvin_console_sid"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if any(key.startswith(p) for p in ("corvin_console", "corvin_gateway", "forge")):
            del sys.modules[key]


@dataclass
class ConsoleSandbox:
    client: Any  # fastapi.testclient.TestClient
    home: Path
    tenant_id: str
    tenant_home: Path
    chain: Path
    sid: str
    csrf: str

    @property
    def csrf_headers(self) -> dict[str, str]:
        return {"x-csrf-token": self.csrf}

    def events_on_disk(self) -> list[dict[str, Any]]:
        """Every ADR-0314 learning event persisted under the sandbox tenant."""
        out: list[dict[str, Any]] = []
        for f in sorted((self.tenant_home / "learning" / "events").glob("*.jsonl")):
            out.extend(json.loads(line) for line in f.read_text().splitlines() if line.strip())
        return out

    def chain_records(self) -> list[dict[str, Any]]:
        if not self.chain.exists():
            return []
        return [json.loads(line) for line in self.chain.read_text().splitlines() if line.strip()]


@contextmanager
def console_client(tmp_path: Path, tenant_id: str = "_default") -> Iterator[ConsoleSandbox]:
    home = tmp_path / "corvin_home"
    tenant_home = home / "tenants" / tenant_id
    for sub in ("global/auth", "global/forge", "global/console/sessions"):
        (tenant_home / sub).mkdir(parents=True, exist_ok=True)
    chain = home / "audit.jsonl"
    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    os.environ["VOICE_AUDIT_PATH"] = str(chain)
    # ``boot_learning_registry`` boots the process-global ACP registry; restore
    # whatever was there before so a later test's ``recent_outcomes(store=None)``
    # cannot resolve this sandbox's (by then deleted) emitter.
    try:
        from core.skills import skill_registry_phase1 as _reg
        prev_registry = getattr(_reg, "_global_registry", None)
    except Exception:  # noqa: BLE001 — stripped install
        _reg, prev_registry = None, None
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set(COOKIE_NAME, rec.sid)
            yield ConsoleSandbox(
                client=client, home=home, tenant_id=tenant_id, tenant_home=tenant_home,
                chain=chain, sid=rec.sid, csrf=csrf,
            )
    finally:
        if _reg is not None:
            _reg._global_registry = prev_registry
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules()


def write_outcome(sb: ConsoleSandbox, *, task_id: str, success: bool, tenant_id: str | None = None) -> str:
    """Persist one real OUTCOME event through the audit-first store; returns its event_id."""
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    from core.learning.outcome_sink import OUTCOME_SKILL_ID

    tenant = tenant_id or sb.tenant_id
    store = EventStore(sb.home / "tenants" / tenant, tenant_id=tenant)
    event = LearningEvent.create(
        event_type=EventType.OUTCOME,
        skill_id=OUTCOME_SKILL_ID,
        tenant_id=tenant,
        signal={"task_id": task_id, "status": "completed" if success else "failed", "success": success},
        lom="tests/learning/console_client.py:write_outcome",
    )
    # The core chain refuses a record tagged with a tenant other than the
    # process tenant (F-A6) — a foreign tenant's event can only be produced
    # from that tenant's own process context, which is what we emulate here.
    prev = os.environ.get("CORVIN_TENANT_ID")
    os.environ["CORVIN_TENANT_ID"] = tenant
    try:
        store.write_event(event)
    finally:
        if prev is None:
            os.environ.pop("CORVIN_TENANT_ID", None)
        else:
            os.environ["CORVIN_TENANT_ID"] = prev
    return event.event_id


def boot_learning_registry(sb: ConsoleSandbox):
    """Boot the ACP registry with a learning emitter over the sandbox store.

    ``outcome_sink.learning_emitter()`` / ``recent_outcomes()`` — what the
    optimizer epoch behind ``POST learning/feedback`` reads — resolve the
    BOOTED registry's emitter, so a test that expects the optimizer to see
    outcomes must boot exactly like the console does. Returns the emitter;
    call ``.stop()`` to flush.
    """
    from core.learning.event_emitter import EventEmitter
    from core.learning.event_store import EventStore
    from core.skills.boot import boot_skills
    from core.skills.skill_registry_phase1 import LearningEmitterBackend

    emitter = EventEmitter(EventStore(sb.tenant_home, tenant_id=sb.tenant_id))
    boot_skills(sb.tenant_id, audit_emit=lambda *_a, **_k: None,
                learning_backend=LearningEmitterBackend(emitter, session_id="test"))
    return emitter
