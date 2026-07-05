"""FastAPI app for the Corvin Gateway.

ADR-0007 Phase 2.2 + 2.3 — wires the bearer-token resolver, the run
registry and the engine dispatcher into the REST surface:

* ``POST /v1/tenants/{tid}/runs`` — accepts an AWP-shaped Run,
  persists it, schedules the engine dispatch, returns 202 +
  ``{"run_id": "...", "status": "accepted"}``.
* ``GET /v1/tenants/{tid}/runs/{run_id}`` — returns the run record
  (status + result once dispatched).

Auth contract
-------------

For local deployments, no auth header is required — the loopback
binding is the security boundary. Cloud deployments validate JWT
(OAuth/OIDC).

Lifespan + dispatcher
---------------------

The FastAPI ``lifespan`` handler creates a single ``RunDispatcher``
per gateway process and stashes it on ``app.state.dispatcher``.
On shutdown, in-flight dispatches are awaited via
``dispatcher.drain()`` so a clean SIGTERM never leaves a half-run
in ``running`` state.

Tests that drive the app via :class:`fastapi.testclient.TestClient`
inside a ``with`` block get the lifespan for free — the dispatcher
is constructed, the test issues POST / GET pairs, and the test
context-manager exit awaits the drain.

Webhooks + SSE are deferred to Phases 2.4 / 2.5.

Single-operator opt-out
-----------------------

The Gateway never auto-starts. Operators wire ``uvicorn
corvin_gateway.app:app`` into their service manager only when they
genuinely want the multi-tenant surface; single-operator deployments
keep the bridges' inbox-based interface and never expose any port.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from starlette.requests import HTTPConnection
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError

from fastapi.responses import PlainTextResponse

from . import __version__
from . import audit_metrics as _audit_metrics
from . import durable_queue as _durable_queue
from . import rate_limit as _rate_limit
from .auth import _audit as _auth_audit  # noqa: F401 — kept for plugin compat
from .dispatcher import RunDispatcher
from .oidc import looks_like_jwt, resolve_jwt  # available for cloud OIDC wiring
from . import scim as _scim
from .runs import (
    RunNotFound,
    RunRecord,
    RunRegistry,
    RunRequest,
    RunStoreMalformed,
    TERMINAL_STATES,
)
from .sse import format_sse_frame


# ── Optional JWT guard ───────────────────────────────────────────────
#
# For local deployments: no Authorization header → pass through.
# For cloud deployments: a JWT (OAuth/OIDC) is validated if present;
# an invalid/expired JWT returns 401 to prevent token downgrade attacks.
# Static atlr_* tokens have been removed entirely.


async def _jwt_guard(request: HTTPConnection) -> None:
    """Global dependency: validate JWT when present, allow auth-free requests."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return  # no Bearer header → local deployment, allow through
    presented = auth_header[7:].strip()
    if not presented or not looks_like_jwt(presented):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "non-jwt-bearer-rejected"},
            headers={"WWW-Authenticate": 'Bearer realm="corvin-gateway"'},
        )
    resolved = resolve_jwt(presented)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "invalid-jwt"},
            headers={"WWW-Authenticate": 'Bearer realm="corvin-gateway"'},
        )


# ── Lifespan + app instance ──────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the dispatcher on startup, drain in-flight on shutdown.

    The dispatcher is owned by the app, not the request — every
    request shares the same instance so a single dispatch queue
    serialises across the whole process.
    """
    # Tests that want to inject a fake engine MUST do so BEFORE
    # entering the ``with TestClient(app):`` context. They set
    # ``app.state.dispatcher`` themselves; this lifespan honours
    # an existing dispatcher and only constructs a default when
    # none is present.
    # Activate the installed license in THIS process. The adapter loads it at
    # boot (adapter.py), but the gateway/console process did not — so a valid
    # <corvin_home>/global/license.key (or CORVIN_LICENSE_KEY) was ignored and
    # the console reported `free` regardless of the customer's tier (paid
    # features stayed gated). load_license_from_env() is idempotent + best-effort
    # (absence simply leaves the free-tier fallback).
    try:
        from license.validator import load_license_from_env as _lic_load
        _lic_load()
    except Exception:
        pass
    if not hasattr(app.state, "dispatcher") or app.state.dispatcher is None:
        app.state.dispatcher = RunDispatcher()
    if not hasattr(app.state, "rate_limiter") or app.state.rate_limiter is None:
        app.state.rate_limiter = _rate_limit.RateLimiter()
    # Phase 7.1 — recover any pending runs from the durable queue
    # the previous process accepted but never finished.
    try:
        await app.state.dispatcher.recover_pending()
    except Exception:
        pass
    # L44 house-rules classifier health check — surfaces missing Ollama models
    # at startup so operators know BEFORE users hit fail-closed blocks.
    try:
        import sys as _sys, os as _os
        _bridges = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                 "..", "..", "..", "..",
                                 "operator", "bridges", "shared")
        _bridges = _os.path.normpath(_bridges)
        if _bridges not in _sys.path:
            _sys.path.insert(0, _bridges)
        from house_rules import house_rules_boot_health_check as _hr_boot  # type: ignore
        import logging as _logging
        _hr_boot(log_fn=_logging.getLogger("corvin.house_rules").warning)
    except Exception:
        pass  # best-effort — never blocks gateway startup

    # ACO Boot-Healer: scan + repair stalled sessions after install / restart.
    # Runs as a non-blocking background task; never delays the lifespan.
    _healer_task = None
    try:
        from corvin_console.aco.boot_healer import start_boot_healer as _start_healer
        _healer_task = _start_healer()
    except Exception:
        pass  # console package absent or import error — gateway still starts

    # Telemetry heartbeat — 5-min daemon thread, default-ON/opt-out (ADR-0180).
    try:
        from corvin_console.aco.heartbeat import start_heartbeat_thread as _start_hb
        import forge.paths as _fp
        _start_hb(_fp.corvin_home())
    except Exception:
        pass  # best-effort — never blocks startup
    try:
        yield
    finally:
        if _healer_task is not None:
            _healer_task.cancel()
        dispatcher: RunDispatcher | None = getattr(
            app.state, "dispatcher", None,
        )
        if dispatcher is not None:
            # Bounded drain so a hung engine doesn't wedge shutdown
            # forever. The asyncio.wait_for in dispatcher._run_one
            # already caps individual runs; this is the global guard.
            await dispatcher.drain(timeout=30.0)


app = FastAPI(
    title="Corvin Gateway",
    version=__version__,
    description=(
        "Multi-tenant REST surface for the Corvin framework "
        "(ADR-0007). Opt-in; single-operator deployments never "
        "enable this."
    ),
    docs_url=None,    # No public OpenAPI docs in Phase 2 — the
    redoc_url=None,   # surface is still in flux. Phase 2.6 will
    openapi_url=None, # decide on doc exposure as part of closure.
    lifespan=_lifespan,
    dependencies=[Depends(_jwt_guard)],
)


# ── ADR-0015 — corvin-console plugin opt-in mount ────────────────────
#
# Owner-self-service web UI. Opt-in pattern: absent venv → ImportError
# → silently skipped. When present:
#   * /v1/console/* — REST API
#   * /console/*    — React SPA (web-next/dist)
try:
    import sys as _sys2
    from pathlib import Path as _Path2
    _console_path = _Path2(__file__).resolve().parents[2] / "console"
    if str(_console_path) not in _sys2.path:
        _sys2.path.insert(0, str(_console_path))
    from corvin_console import app as _console_app  # type: ignore[import-not-found]
    app.include_router(_console_app.router, prefix="/v1/console")
    _console_app.mount_static(app)
    # Local stats HTML dashboard — bare /local-stats (outside /console/ SPA prefix)
    from fastapi.responses import HTMLResponse as _HTMLResponse
    from corvin_console.standalone import _LOCAL_STATS_HTML as _ls_html  # type: ignore
    @app.get("/local-stats", include_in_schema=False)
    def _local_stats_page() -> _HTMLResponse:
        return _HTMLResponse(content=_ls_html)
except ImportError:
    pass
except Exception as _plugin_exc:
    import logging as _logging
    _logging.getLogger(__name__).warning("plugin load failed: %r", _plugin_exc)


# ── ADR-0017 Phase III — corvin-license plugin opt-in mount ──────────
#
# License-gate. Same opt-in pattern: absent venv / missing pubkey →
# silently skipped, deployment behaves as full Apache-2.0 free tier
# with no blocking. When present:
#   * /v1/license/* — REST API (status, healthz, version)
try:
    import sys as _sys3
    from pathlib import Path as _Path3
    _license_path = _Path3(__file__).resolve().parents[2] / "license"
    if str(_license_path) not in _sys3.path:
        _sys3.path.insert(0, str(_license_path))
    from corvin_license import app as _license_app  # type: ignore[import-not-found]
    app.include_router(_license_app.router, prefix="/v1/license")
except ImportError:
    pass
except Exception as _plugin_exc:
    import logging as _logging
    _logging.getLogger(__name__).warning("plugin load failed: %r", _plugin_exc)


# ── ADR-0017 Phase V — corvin-enterprise plugin opt-in mount ─────────
#
# Proprietary overlay (distributed via signed .corvin-pkg per
# ADR-0007 Phase 5). Lives OUTSIDE the open-core tree — operator
# installs it under a separate directory the gateway resolves via:
#
#   1. CORVIN_ENTERPRISE_PATH env var (production override)
#   2. <corvin_home>/plugins/corvin-enterprise/ (canonical install)
#   3. ~/.corvin/plugins/corvin-enterprise/ (XDG-style default)
#   4. ~/projects/corvin-enterprise/ (developer working-tree fallback)
#
# Same opt-in pattern as the other plugins: absent / import-broken →
# silently skipped. The gateway works exactly as before; free-tier
# deployments keep every Apache-core route unconditionally.
#
# License-gate semantics ride INSIDE the plugin — its own
# `_try_mount_scheduled_reports()` consults
# `corvin_license.has_feature(flag)` at mount time and only
# registers premium routes when the active license carries the
# matching feature flag.
try:
    import os as _os4
    import sys as _sys4
    from pathlib import Path as _Path4
    _ent_candidates: list[_Path4] = []
    _ent_env = _os4.environ.get("CORVIN_ENTERPRISE_PATH")
    if _ent_env:
        _ent_candidates.append(_Path4(_ent_env))
    _ent_home_env = _os4.environ.get("CORVIN_HOME")
    if _ent_home_env:
        _ent_candidates.append(
            _Path4(_ent_home_env) / "plugins" / "corvin-enterprise"
        )
    _ent_candidates.append(
        _Path4.home() / ".corvin" / "plugins" / "corvin-enterprise"
    )
    _ent_candidates.append(
        _Path4.home() / "projects" / "corvin-enterprise"
    )
    for _ent_path in _ent_candidates:
        if (_ent_path / "corvin_enterprise" / "app.py").exists():
            if str(_ent_path) not in _sys4.path:
                _sys4.path.insert(0, str(_ent_path))
            from corvin_enterprise import (  # type: ignore[import-not-found]
                app as _enterprise_app,
            )
            app.include_router(_enterprise_app.router, prefix="/v1/enterprise")
            break
except ImportError:
    pass
except Exception as _plugin_exc:
    import logging as _logging
    _logging.getLogger(__name__).warning("plugin load failed: %r", _plugin_exc)


# ── Layer 38 — A2A inbound receive endpoint ──────────────────────────
#
# POST /v1/a2a/receive — accepts a signed TaskEnvelope from a trusted
# remote origin. HMAC-authenticated; no bearer token required.
# Transport wiring for ADR-0048 (RemoteTriggerReceiver, M1+).
#
# The shared module lives at operator/bridges/shared/ relative to the
# repo root; the gateway's PYTHONPATH already includes /opt/corvin-repo,
# so we locate it via __file__ parents to work in both dev and Docker.
try:
    import sys as _sys_a2a
    import os as _os_a2a
    from pathlib import Path as _Path_a2a
    _a2a_shared = _Path_a2a(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
    if str(_a2a_shared) not in _sys_a2a.path:
        _sys_a2a.path.insert(0, str(_a2a_shared))
    from remote_trigger_receiver import RemoteTriggerReceiver as _RemoteTriggerReceiver  # type: ignore[import-not-found]

    # Engine selector — operators pick which engine M2-spawn uses:
    #   CORVIN_A2A_ENGINE=claude   → ClaudeCodeEngine (default; needs `claude` in PATH)
    #   CORVIN_A2A_ENGINE=compute  → DeterministicComputeEngine (CSV+matplotlib)
    _a2a_engine_name = _os_a2a.environ.get("CORVIN_A2A_ENGINE", "claude").strip().lower()
    _a2a_engine_factory = None
    if _a2a_engine_name == "compute":
        from a2a_compute_engine import DeterministicComputeEngine as _DCE  # type: ignore[import-not-found]
        _a2a_engine_factory = lambda: _DCE()

    _a2a_receiver = _RemoteTriggerReceiver(engine_factory=_a2a_engine_factory)
    _A2A_AVAILABLE = True
except Exception:
    _A2A_AVAILABLE = False
    _a2a_receiver = None


@app.post("/v1/a2a/receive")
async def a2a_receive(request: Request) -> JSONResponse:
    """Layer 38 — A2A inbound receive.

    HMAC-authenticated (no bearer token). Validates the signed
    TaskEnvelope, anchors the exchange in the L16 audit chain, and
    returns a signed ResponseEnvelope. ADR-0048.
    """
    if not _A2A_AVAILABLE or _a2a_receiver is None:
        raise HTTPException(
            status_code=503,
            detail={"reason": "a2a_not_configured"},
        )
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"reason": "invalid_json"},
        )
    response = _a2a_receiver.receive(body)
    return JSONResponse(content=response.to_dict())


# ── Auth note ────────────────────────────────────────────────────────
#
# Static atlr_* token auth has been removed. For local deployments the
# loopback binding (127.0.0.1) is the security boundary. OIDC/JWT via
# resolve_jwt() is available and will be enforced in the cloud phase.


# ── Endpoints ────────────────────────────────────────────────────────


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/console/favicon.svg", status_code=302)


@app.get("/", include_in_schema=False)
async def root_redirect():
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/console/", status_code=302)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Liveness probe. Unauthenticated by design — operators behind
    a reverse proxy ACL the endpoint there, not here."""
    return {"status": "ok", "version": __version__}


@app.post(
    "/v1/tenants/{tid}/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_run(
    tid: str,
    payload: dict[str, Any],
    request: Request,
) -> JSONResponse:
    """Accept an AWP-shaped Run and schedule engine dispatch.

    Returns 202 with ``{"run_id": "...", "status": "accepted"}``.
    The dispatcher (Phase 2.3) drives the run through ``running`` to
    one of ``completed`` / ``failed`` / ``budget_exceeded``; clients
    poll the GET endpoint to observe transitions.
    """

    # Phase 7.2: per-tenant rate-limit gate. Fires BEFORE we burn
    # cycles validating the body — a throttled tenant gets a fast
    # 429 with audit emission, never reaches engine dispatch.
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        allowed, bucket = limiter.check(tid)
        if not allowed:
            if bucket is not None:
                _rate_limit.audit_rate_limited(
                    tid,
                    capacity=bucket.capacity,
                    tokens_remaining=bucket.tokens,
                )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"reason": "rate-limited"},
            )

    try:
        run_request = RunRequest.model_validate(payload)
    except ValidationError as exc:
        # Pydantic v2 errors carry Python objects in the ``ctx`` /
        # ``input`` fields (e.g. the raw ``ValueError`` from a
        # field_validator). Project to JSON-safe primitives so the
        # 422 body can be serialised.
        safe_errors = [
            {
                "loc":  list(e.get("loc", ())),
                "msg":  str(e.get("msg", "")),
                "type": str(e.get("type", "")),
            }
            for e in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "invalid-run-spec", "errors": safe_errors},
        )
    registry = RunRegistry()
    try:
        record = registry.create(tid, run_request)
    except RunStoreMalformed as exc:
        # Tenant directory missing → 500 is the honest answer; this
        # is an operator-provisioning gap, not a client mistake.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "tenant-not-provisioned", "message": str(exc)},
        )

    # Fire-and-forget engine dispatch. Failure to submit is logged
    # in the dispatcher itself; the run stays in ``accepted`` and
    # an operator (or a future janitor) sweeps it later.
    dispatcher: RunDispatcher | None = getattr(
        request.app.state, "dispatcher", None,
    )
    if dispatcher is not None:
        dispatcher.submit(tid, record.run_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"run_id": record.run_id, "status": record.status},
    )


@app.get("/v1/tenants/{tid}/runs/{run_id}/events")
async def stream_run_events(
    tid: str,
    run_id: str,
    request: Request,
) -> StreamingResponse:
    """Stream the run's engine events as Server-Sent Events.

    The response is ``Content-Type: text/event-stream``. Each engine
    event arrives as one SSE frame::

        event: text_delta
        data: {"type": "text_delta", "text": "Hello", ...}

    The stream ends with one final ``run.<status>`` event when the
    dispatcher reaches a terminal state, then closes the connection.

    A subscriber that connects mid-run receives the full history
    first (replay), then live events. A subscriber that connects
    AFTER the run has terminated receives the full history followed
    by the terminal event, then disconnects.

    A subscriber for a run that the gateway process has never seen
    (e.g. process restart, run from a different gateway instance)
    receives a one-shot frame with the record's current status as
    derived from disk, then disconnects.
    """
    registry = RunRegistry()
    try:
        record = registry.get(tid, run_id)
    except RunNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "run-not-found"},
        )
    except RunStoreMalformed:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "run-store-malformed"},
        )

    dispatcher = getattr(request.app.state, "dispatcher", None)
    buf = dispatcher.events.get(tid, run_id) if dispatcher is not None else None

    async def event_generator():
        # Buffer-aware path — covers both live runs and runs that
        # completed in this gateway process.
        if buf is not None:
            try:
                async for event in buf.subscribe():
                    yield format_sse_frame(event)
                    # Honour client disconnects: starlette signals
                    # disconnect through `await request.is_disconnected()`.
                    if await request.is_disconnected():
                        return
                return
            except Exception:
                # Fall through to the on-disk one-shot path so the
                # client still gets a meaningful final event rather
                # than a silent EOF.
                pass

        # Fallback for runs without an in-memory buffer (process
        # restart, run created in a different gateway instance).
        # Deliver the current record state as one terminal frame.
        # If the run is still accepted/running we can't follow it
        # in real time — clients should re-issue the request once
        # the run has started dispatching, or fall back to
        # polling GET /runs/{run_id}.
        try:
            fresh = registry.get(tid, run_id)
        except (RunNotFound, RunStoreMalformed):
            return
        ev_type = (
            f"run.{fresh.status}" if fresh.status in TERMINAL_STATES
            else "run.snapshot"
        )
        yield format_sse_frame({
            "type":   ev_type,
            "status": fresh.status,
            "result": fresh.result,
            "error":  fresh.error,
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


# ── SCIM 2.0 stub (Phase 3.5) ────────────────────────────────────────


_SCIM_BASE = "/v1/tenants/{tid}/scim/v2"


def _scim_location(request: Request, tid: str, uid: str) -> str:
    return str(request.url_for("scim_get_user", tid=tid, uid=uid))


@app.get("/v1/tenants/{tid}/scim/v2/Users")
def scim_list_users(
    tid: str, request: Request,
) -> dict[str, Any]:
    """SCIM 2.0 List Users — minimal, no filter / no pagination."""
    store = _scim.ScimUserStore()
    users = store.list(tid)
    resources = [
        _scim._user_to_scim(
            uid, entry,
            location=_scim_location(request, tid, uid),
        )
        for uid, entry in users.items()
    ]
    return {
        "schemas":      [_scim.SCIM_LIST_SCHEMA],
        "totalResults": len(resources),
        "Resources":    resources,
    }


@app.post(
    "/v1/tenants/{tid}/scim/v2/Users",
    status_code=status.HTTP_201_CREATED,
)
def scim_create_user(
    tid: str,
    payload: dict[str, Any],
    request: Request,
) -> JSONResponse:
    store = _scim.ScimUserStore()
    try:
        uid, entry = store.create(tid, payload)
    except _scim.ScimValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_scim.scim_error(400, str(exc), scim_type="invalidValue"),
        )
    except _scim.ScimConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_scim.scim_error(409, str(exc), scim_type="uniqueness"),
        )
    except _scim.ScimStoreMalformed as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_scim.scim_error(500, str(exc)),
        )
    body = _scim._user_to_scim(
        uid, entry, location=_scim_location(request, tid, uid),
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=body,
        headers={"Location": body["meta"]["location"]},
    )


@app.get(
    "/v1/tenants/{tid}/scim/v2/Users/{uid}",
    name="scim_get_user",
)
def scim_get_user(
    tid: str, uid: str, request: Request,
) -> JSONResponse:
    entry = _scim.ScimUserStore().get(tid, uid)
    if entry is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_scim.scim_error(404, f"no user {uid!r}"),
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=_scim._user_to_scim(
            uid, entry, location=_scim_location(request, tid, uid),
        ),
    )


@app.delete(
    "/v1/tenants/{tid}/scim/v2/Users/{uid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def scim_delete_user(
    tid: str, uid: str,
) -> JSONResponse:
    if _scim.ScimUserStore().delete(tid, uid):
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_scim.scim_error(404, f"no user {uid!r}"),
    )


@app.patch("/v1/tenants/{tid}/scim/v2/Users/{uid}")
def scim_patch_user(
    tid: str,
    uid: str,
    payload: dict[str, Any],
    request: Request,
) -> JSONResponse:
    """SCIM 2.0 PatchOp (RFC 7644 §3.5.2) — Phase 3.6."""
    store = _scim.ScimUserStore()
    try:
        updated = store.patch(tid, uid, payload)
    except _scim.ScimValidationError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_scim.scim_error(400, str(exc), scim_type="invalidValue"),
        )
    except _scim.ScimConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_scim.scim_error(409, str(exc), scim_type="uniqueness"),
        )
    except _scim.ScimStoreMalformed as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_scim.scim_error(500, str(exc)),
        )
    if updated is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_scim.scim_error(404, f"no user {uid!r}"),
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=_scim._user_to_scim(
            uid, updated, location=_scim_location(request, tid, uid),
        ),
    )


# ── Metrics endpoint (Phase 6.2) ─────────────────────────────────────


@app.get(
    "/v1/tenants/{tid}/metrics",
    response_class=PlainTextResponse,
)
def tenant_metrics(
    tid: str,
    request: Request,
) -> PlainTextResponse:
    """Prometheus exposition format projection of the tenant's audit chain.

    Read-only. No state mutation, no audit event on scrape (would
    pollute the chain at scrape-rate × tenant-count cardinality).

    Optional ``?since=<duration>`` query param trims the aggregation
    window. Duration syntax: ``30s``, ``5m``, ``2h``, ``7d``, or a
    bare integer (seconds). Default: all-time.

    Per-tenant scoping is structural — the chain file for *tid* is
    the only input, so a per-tenant scrape can never leak another
    tenant's activity. Cross-tenant aggregates require a separate
    Gateway-operator endpoint (out of scope for Phase 6).
    """
    since: float | None = None
    since_q = request.query_params.get("since")
    if since_q:
        try:
            seconds = _audit_metrics.parse_duration(since_q)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"reason": "invalid-since", "message": str(exc)},
            )
        # Clamp to a reasonable upper bound; queries longer than 30 d
        # are not useful for live monitoring + would force a full
        # chain rescan on every cache miss.
        seconds = min(seconds, 30 * 86400.0)
        import time as _time
        since = _time.time() - seconds
    body = _audit_metrics.render(tid, since=since)
    return PlainTextResponse(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/v1/tenants/{tid}/runs/{run_id}")
def get_run(tid: str, run_id: str) -> dict[str, Any]:
    """Return the on-disk record for *run_id* in tenant *tid*."""
    registry = RunRegistry()
    try:
        record = registry.get(tid, run_id)
    except RunNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "run-not-found"},
        )
    except RunStoreMalformed:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "run-store-malformed"},
        )
    return record.to_dict()
