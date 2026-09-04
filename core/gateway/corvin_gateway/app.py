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

import os
import sys
from pathlib import Path


def _plugin_package_absent(err: ImportError) -> bool:
    """True only when the ``corvin_plugins`` package itself is not installed.

    Shared predicate for the two shipped hosts (gateway + console standalone):
    a ``ModuleNotFoundError`` naming exactly the top-level package is a stripped
    install and is tolerated; anything else (missing submodule, renamed symbol,
    an exception raised while importing the package) means the compliance
    mechanism is present but broken, and the boot must fail closed.
    """
    return isinstance(err, ModuleNotFoundError) and err.name == "corvin_plugins"


# CRITICAL: Set CORVIN_HOME BEFORE any imports that call corvin_home().
# When the gateway runs as a service from within a repo checkout, _forge_paths.corvin_home()
# would detect repo context and return repo/.corvin, breaking session storage symmetry.

# Put the CorvinOS repo ROOT on sys.path so the `core.<subpackage>` imports
# resolve (core/console/... does `from core.learning import ...`).
#
# `core/` ITSELF must NEVER go on sys.path. Its subdirectories carry generic
# top-level names -- audit, console, context, integration, agent, license,
# features, monitoring -- that shadow the bridge/PYTHONPATH modules of the same
# name. Adding it on 2026-08-31 (commit 6ab97601) made the tripwire's
# `import audit` resolve to core/audit/ instead of
# operator/bridges/shared/audit.py; core/audit has no `audit_path`, so
# audit_writer_reachable + audit_chain_intact both failed with AttributeError
# and the boot tripwire refused to boot -- the console served nothing for ~45
# restart cycles. APPEND, never insert(0): the repo root also exposes generic
# names (tests/, scripts/, docs/), and existing sys.path entries must win.
_app_file = Path(__file__).resolve()
_corvin_root = _app_file.parent.parent.parent.parent  # Navigate to CorvinOS root
if str(_corvin_root) not in sys.path and _corvin_root.exists():
    sys.path.append(str(_corvin_root))
# This must run BEFORE any module imports that use corvin_home() (e.g., _audit_metrics).
if not os.environ.get("CORVIN_HOME"):
    os.environ["CORVIN_HOME"] = str(Path.home() / ".corvin")

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from starlette.requests import HTTPConnection
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError

from fastapi.responses import PlainTextResponse
import ipaddress as _ipaddress

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


# ── JWT guard ────────────────────────────────────────────────────────
#
# Adversarial review E-09 (2026-09-03). Two rules, both fail-closed:
#
# 1. **Loopback peer → local operator.** A request arriving from 127.0.0.0/8
#    or ::1 without a Bearer header is the local deployment's owner (the
#    loopback binding is the security boundary, as documented below). ANY
#    other peer — a LAN/Internet address, a proxy-rewritten address, or an
#    unparseable one — MUST present a valid JWT; without one the request is
#    401 before any tenant route runs. Previously a missing Bearer header
#    passed through regardless of peer, so binding the gateway to a
#    non-loopback address exposed every ``/v1/tenants/{tid}/*`` route
#    (SCIM, runs, metrics) unauthenticated.
# 2. **Path tenant is bound to the principal.** When a JWT is presented, the
#    ``tid`` path parameter must equal the tenant the token resolved to
#    (``resolve_jwt`` takes it from the issuer's ``tenant_claim``); a mismatch
#    is 403. A token for tenant A can never read or write tenant B by
#    choosing B in the URL.
#
# A present-but-invalid/expired JWT is always 401 (token-downgrade guard).
# Static atlr_* tokens have been removed entirely.

_LOOPBACK_HOSTS = frozenset({"localhost"})


def _peer_is_loopback(request: HTTPConnection) -> bool:
    """True only for a parseable loopback peer address. Fail-closed otherwise."""
    client = request.client
    if client is None:
        return False
    host = (client.host or "").strip()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return _ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _jwt_guard(request: HTTPConnection) -> None:
    """Global dependency (see the rules above).

    Stores the resolved token on ``request.state.principal`` (``None`` for a
    loopback caller without a token) so route handlers can attribute work.
    """
    auth_header = request.headers.get("authorization", "")
    path_tid = request.path_params.get("tid") if request.path_params else None
    if not auth_header.lower().startswith("bearer "):
        if _peer_is_loopback(request):
            request.state.principal = None
            return  # loopback peer, no Bearer → local operator
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "bearer-required-for-non-loopback-peer"},
            headers={"WWW-Authenticate": 'Bearer realm="corvin-gateway"'},
        )
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
    if path_tid is not None and path_tid != resolved.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason": "tenant-mismatch"},
        )
    request.state.principal = resolved


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

    # ── ADR-0232/0233 — compliance tripwires (FAIL-CLOSED) ───────────────────
    # Deliberately NOT wrapped in `except: pass` like the best-effort blocks
    # around it: if the mandatory core audit writer is unreachable or its hash
    # chain does not verify, the platform must refuse to serve rather than run
    # without a GDPR Art. 30/32 trail. There is no override switch by design.
    # An absent plugin package (stripped install) is not a failure — only a
    # broken mechanism is; assert_compliance() distinguishes the two.
    _plugins_loaded: list[str] = []
    _health_collector = None
    try:
        from corvin_plugins.bootstrap import boot_platform as _boot_platform
    except ImportError as _pkg_err:
        # ADR-0232/0233 — only an ABSENT package is tolerated here. A package
        # that is present but broken (renamed submodule, missing symbol) is a
        # broken mechanism and must fail the boot; treating every ImportError
        # as "absent" turned the tripwire into a no-op once (2026-09-01, when
        # bootstrap's provider imports broke and both hosts kept serving).
        if not _plugin_package_absent(_pkg_err):
            raise
        _boot_platform = None  # type: ignore[assignment]
        import logging as _tw_log
        _tw_log.getLogger("corvin.compliance.tripwire").debug(
            "corvin_plugins absent — compliance tripwires not available"
        )
    if _boot_platform is not None:
        # Tripwires -> plugin load -> post-boot tripwire, in that order and with
        # no override. The sequence is shared with corvin_console.standalone so
        # the two shipped hosts cannot drift; see boot_platform's docstring for
        # why each step sits where it does.
        _plugins_loaded = _boot_platform()  # raises TripwireError -> boot aborts

        # ADR-0231 Stage 2/3 — health polling + self-healing are started BY
        # boot_platform() (corvin_plugins.bootstrap.start_health_monitoring),
        # so the standalone console gets them too. This lifespan only keeps
        # the handle for the shutdown ordering below.
        from corvin_plugins.bootstrap import health_collector as _health_collector_handle

        _health_collector = _health_collector_handle()
    if not hasattr(app.state, "dispatcher") or app.state.dispatcher is None:
        app.state.dispatcher = RunDispatcher()
    if not hasattr(app.state, "rate_limiter") or app.state.rate_limiter is None:
        app.state.rate_limiter = _rate_limit.RateLimiter()
    # L5 k=2 — OperatorApprovalGate for learning feedback control
    # Initialize approval gate with audit backend for operator-gated skill learning
    try:
        from core.skills.feedback_stability import OperatorApprovalGate as _OperatorApprovalGate
        from corvin_plugins.providers import audit_backend as _approval_audit_backend

        _approval_gate = _OperatorApprovalGate(
            tenant_id="_default",
            auto_approval_confidence_threshold=0.8,
            approval_ttl_hours=12,
            audit_backend=_approval_audit_backend,
            corvin_home=os.environ.get("CORVIN_HOME"),
        )
        app.state.approval_gate = _approval_gate

        # Wire approval gate into routes (enable /v1/approvals endpoints)
        from core.gateway.routes import approval_routes as _approval_routes_module
        _approval_routes_module.set_approval_gate(_approval_gate)

        import logging as _appr_log
        _appr_log.getLogger("corvin.approval_gate").info(
            "OperatorApprovalGate initialized for L5 k=2 learning control"
        )
    except Exception as e:
        import logging as _appr_log_err
        _appr_log_err.getLogger("corvin.approval_gate").warning(
            "Failed to initialize OperatorApprovalGate (approval endpoints disabled): %s", e
        )
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
        # Importing corvin_console first is load-bearing: its
        # _operator_bootstrap puts the operator/ subtrees on sys.path in BOTH
        # layouts (repo checkout and wheel-install _vendor). The explicit
        # repo-relative insert below is only the fallback for a checkout
        # without the console package installed. (A previous version used
        # four ".." — one directory above the repo root — so this import
        # only ever worked by sys.path luck.)
        try:
            import corvin_console  # noqa: F401 — sys.path bootstrap side effect
        except Exception:
            pass
        _bridges = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                 "..", "..", "..",
                                 "operator", "bridges", "shared")
        _bridges = _os.path.normpath(_bridges)
        if _bridges not in _sys.path and _os.path.isdir(_bridges):
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
        from corvin_core.aco.boot_healer import start_boot_healer as _start_healer
        _healer_task = _start_healer()
    except Exception:
        pass  # console package absent or import error — gateway still starts

    # Telemetry heartbeat — 5-min daemon thread, default-ON/opt-out (ADR-0180).
    try:
        from corvin_core.aco.heartbeat import start_heartbeat_thread as _start_hb
        import forge.paths as _fp
        _start_hb(_fp.corvin_home())
    except Exception:
        pass  # best-effort — never blocks startup

    # ADR-0191/ADR-0193 — idempotently seed the built-in zero-config tools
    # (image-generation, native browser) into the mcp_manager catalog on
    # every boot, so a genuinely fresh install has them active with no
    # manual registration step (the whole point of "zero config").
    # Marker-based (seed_builtin): first boot installs + activates; later
    # boots only refresh stale interpreter paths and NEVER override an
    # operator's deactivation/uninstall/edits. corvin_console import first —
    # see the health-check block above (wheel-install _vendor bootstrap).
    # Each seeder gets its OWN try/except (one failing must never skip the
    # other) — an earlier revision duplicated the whole path-bootstrap block
    # per seeder verbatim; consolidated to a loop, same independence.
    import os as _os2
    import sys as _sys2
    try:
        import corvin_console  # noqa: F401 — sys.path bootstrap side effect
    except Exception:
        pass
    _mcp_mgr_root = _os2.path.normpath(_os2.path.join(
        _os2.path.dirname(_os2.path.abspath(__file__)),
        "..", "..", "..", "operator", "mcp_manager",
    ))
    if _mcp_mgr_root not in _sys2.path and _os2.path.isdir(_mcp_mgr_root):
        _sys2.path.insert(0, _mcp_mgr_root)
    for _seed_fn_name in ("ensure_imagegen_zero_config", "ensure_corvin_browser"):
        try:
            import mcp_manager.seed_builtin as _seed_builtin
            getattr(_seed_builtin, _seed_fn_name)("_default")
        except Exception:
            pass  # best-effort — never blocks gateway startup, and never
                   # skips the remaining seeders in this loop

    # ADR-0258 Stage 3 — A2A relay listener (best-effort, never blocks
    # startup). Inert unless BOTH the feature flag is on AND a relay URL is
    # configured — the common case (flag off) does nothing here at all,
    # matching every other ship-dark flag in this file. Mirrors
    # corvin_console.standalone's identical block: that host wires the
    # listener but corvin_gateway.app — the process the default systemd
    # unit / Windows autostart actually run (corvin-webui.service,
    # ops/launcher/service_entry.py) — never did, so Stage 3 relay
    # reception was structurally dead on the standard install even with
    # both peers correctly configured (found 2026-08-03 while debugging a
    # live pairing: kids_registered on the operator's own relay never
    # exceeded the sender-side ephemeral reply slots). `_A2A_AVAILABLE`/
    # `_a2a_receiver` are module globals assigned further below (line
    # ~526) — valid: this closure is only CALLED by uvicorn after the
    # whole module has finished executing, and Python resolves free
    # variables in the enclosing scope by name at call time, not at
    # definition time (same reasoning standalone.py's block documents).
    _relay_listener = None
    _relay_task = None
    try:
        from corvin_core import feature_flags as _relay_ff
        import a2a_friendship as _relay_ft  # type: ignore[import-not-found]
        if _A2A_AVAILABLE and _a2a_receiver is not None and _relay_ff.is_enabled("a2a_relay_fallback"):
            _relay_url = _relay_ft.get_my_relay_url()
            if _relay_url:
                import asyncio as _relay_asyncio
                import a2a_relay as _relay_mod  # type: ignore[import-not-found]
                from corvin_console.routes.a2a_pair import (
                    _origins_dir as _relay_origins_dir,
                    _pending_friendships_dir as _relay_pending_dir,
                    _endpoints_dir as _relay_endpoints_dir,
                )
                _relay_listener = _relay_mod.RelayListener(
                    relay_url=_relay_url, receiver=_a2a_receiver,
                    origins_dir=_relay_origins_dir(),
                    pending_dir=_relay_pending_dir(),
                    endpoints_dir=_relay_endpoints_dir(),
                )
                _relay_task = _relay_asyncio.create_task(_relay_listener.run_forever())
                import logging as _relay_log
                _relay_log.getLogger(__name__).info(
                    "A2A relay listener started: %s", _relay_url,
                )
    except Exception:
        import logging as _relay_log
        _relay_log.getLogger(__name__).exception(
            "A2A relay listener failed to start (non-fatal)"
        )

    # KPI Collector Daemon — continuous background metrics emission (Phase 6.3, ADR-0470)
    _metrics_daemon_task = None
    try:
        from core.monitoring import start_daemon as _start_metrics_daemon
        import logging as _metrics_log
        _metrics_daemon_task = await _start_metrics_daemon()
        _metrics_log.getLogger("corvin.metrics.daemon").info(
            "KPI collector daemon started"
        )
    except Exception:
        import logging as _metrics_log
        _metrics_log.getLogger("corvin.metrics.daemon").warning(
            "KPI collector daemon failed to start (non-fatal)", exc_info=True
        )

    try:
        yield
    finally:
        # Stop KPI collector daemon first
        if _metrics_daemon_task is not None:
            try:
                from core.monitoring import stop_daemon as _stop_metrics_daemon
                await _stop_metrics_daemon(timeout=5.0)
                import logging as _metrics_log
                _metrics_log.getLogger("corvin.metrics.daemon").info(
                    "KPI collector daemon stopped"
                )
            except Exception:
                import logging as _metrics_log
                _metrics_log.getLogger("corvin.metrics.daemon").warning(
                    "Error stopping KPI collector daemon (non-fatal)", exc_info=True
                )
        if _relay_listener is not None:
            _relay_listener.stop()
        if _relay_task is not None:
            _relay_task.cancel()
        # Stop polling before unloading, or a poll can land on a plugin that is
        # halfway through on_unload().
        if _health_collector is not None:
            try:
                from corvin_plugins.bootstrap import stop_health_monitoring as _stop_hm

                await _stop_hm()
            except Exception:
                pass
        # Flush the audit fan-out BEFORE unloading: fan-out is a hand-off, so copies
        # can be queued for a backend that is about to detach — and on_unload()
        # discards the queue on purpose (delivering to a detached backend is worse).
        # Without this flush every clean shutdown silently lost the pending copies.
        # Bounded: a wedged sink must not hold the shutdown open, and drain_now()
        # never delivers on this thread, so the bound is real.
        if _plugins_loaded:
            try:
                from corvin_plugins.providers import audit_backend as _ab

                _pending = _ab.drain_now(timeout=5.0)
                if _pending:
                    import logging as _dr_log
                    _dr_log.getLogger("corvin.plugins.bootstrap").info(
                        "flushed %d queued audit copy(ies) before unload", _pending
                    )
            except Exception:
                pass  # best-effort — shutdown must not raise
        # Unload plugins next: on_unload() detaches their provider slots, so a
        # draining request can no longer be routed into a half-torn-down plugin.
        if _plugins_loaded:
            try:
                from corvin_plugins.bootstrap import shutdown as _plugin_shutdown

                _plugin_shutdown(_plugins_loaded)
            except Exception:
                pass  # best-effort — shutdown must not raise
        if _healer_task is not None:
            _healer_task.cancel()
        dispatcher: RunDispatcher | None = getattr(
            app.state, "dispatcher", None,
        )
        if dispatcher is not None:
            # Bounded drain so a hung engine doesn't wedge shutdown
            # forever. The asyncio.wait_for in dispatcher._run_one
            # already caps individual runs; this is the global guard.
            #
            # 15s, not 30s: the systemd unit allows 45s total and uvicorn spends up
            # to 10s waiting for connections plus 5s flushing the audit fan-out
            # before this runs. At 30s the sum was 45s with zero headroom, and with
            # the OLD TimeoutStopSec=15 the whole cleanup was unreachable — every
            # restart was a SIGKILL. Whenever this number changes, check
            # TimeoutStopSec in core/gateway/systemd/corvin-webui.service.
            await dispatcher.drain(timeout=15.0)


# Ensure CORVIN_HOME is set to ~/.corvin for service mode (same as standalone.py).
# This prevents path divergence when the gateway runs as a service from within a
# repo checkout — _forge_paths.corvin_home() would detect repo context and return
# repo/.corvin, breaking session storage reader/writer symmetry. When CORVIN_HOME is
# explicitly set (e.g. in tests or dev mode), respect it; otherwise, default to user home.
if not os.environ.get("CORVIN_HOME"):
    os.environ["CORVIN_HOME"] = str(Path.home() / ".corvin")


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
# ── L5 k=2 Approval Routes ──────────────────────────────────────────
#
# REST API for operator-gated learning control
try:
    from core.gateway.routes.approval_routes import approval_router
    app.include_router(approval_router)
except Exception as _appr_routes_err:
    import logging as _appr_routes_log
    _appr_routes_log.getLogger(__name__).warning(
        "Failed to include approval routes: %s", _appr_routes_err
    )

try:
    import sys as _sys2
    from pathlib import Path as _Path2
    _console_path = _Path2(__file__).resolve().parents[2] / "console"
    if str(_console_path) not in _sys2.path:
        _sys2.path.insert(0, str(_console_path))
    from corvin_console import app as _console_app  # type: ignore[import-not-found]
    app.include_router(_console_app.router, prefix="/v1/console")
    _console_app.mount_static(app)
    # ADR-0241/0243: in headless mode this process serves no browser surface.
    # mount_static() already declines the SPA; the HTML dashboards below are the
    # OTHER browser surfaces on this app and have to make the same decision, or
    # "headless" would mean "the SPA is gone and two full HTML pages remain".
    _headless = False
    try:
        _headless = _console_app.headless_enabled()
    except Exception:  # noqa: BLE001 — unreadable flag means "serve as usual"
        pass
    if not _headless:
        # Local stats HTML dashboard — bare /local-stats (outside /console/ SPA prefix)
        from fastapi.responses import HTMLResponse as _HTMLResponse
        from corvin_console.standalone import _LOCAL_STATS_HTML as _ls_html  # type: ignore
        @app.get("/local-stats", include_in_schema=False)
        def _local_stats_page() -> _HTMLResponse:
            return _HTMLResponse(content=_ls_html)

        # Operator dashboards — bare /op.html, /analytics.html, /telemetry.html
        # (outside SPA). Dev-machine convenience only: the sibling Corvin-Website
        # checkout does not exist on real installs, so these routes are never
        # registered there.
        #
        # INSIDE the headless guard, deliberately: these are three full HTML pages,
        # so "the checkout exists" is not a sufficient gate for them. Registered at
        # the same indentation as /local-stats but outside its `if`, they survived
        # headless mode entirely — and, because _HTMLResponse was imported inside
        # that `if`, answered 500 (NameError) rather than 404 on a dev machine.
        _website_root = _Path2(__file__).resolve().parents[4] / "Corvin-Website"
        if _website_root.exists():
            _op_html_path = _website_root / "op.html"
            _analytics_html_path = _website_root / "analytics.html"
            _telemetry_html_path = _website_root / "telemetry.html"

            if _op_html_path.exists():
                @app.get("/op.html", include_in_schema=False)
                def _op_dashboard() -> _HTMLResponse:
                    return _HTMLResponse(content=_op_html_path.read_text(encoding="utf-8"))

            if _analytics_html_path.exists():
                @app.get("/analytics.html", include_in_schema=False)
                def _analytics_dashboard() -> _HTMLResponse:
                    return _HTMLResponse(content=_analytics_html_path.read_text(encoding="utf-8"))

            if _telemetry_html_path.exists():
                @app.get("/telemetry.html", include_in_schema=False)
                def _telemetry_dashboard() -> _HTMLResponse:
                    return _HTMLResponse(content=_telemetry_html_path.read_text(encoding="utf-8"))

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


@app.post("/v1/a2a/ping")
async def a2a_ping(request: Request) -> JSONResponse:
    """ADR-0199 — lightweight peer-liveness check (receiver side).

    HMAC-authenticated signed probe; responds with a recv_key-signed body
    echoing task_id=ping_id. Delegates to the SAME shared core as the
    stdlib server (a2a_http_server.process_ping_request) so the two
    backends cannot drift — ADR-0199: "Both backends ship together or the
    feature doesn't ship."
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
    from a2a_http_server import process_ping_request  # type: ignore[import-not-found]
    status_code, payload = process_ping_request(body, _a2a_receiver)
    return JSONResponse(content=payload, status_code=status_code)


@app.post("/v1/a2a/friendship-ack")
async def a2a_friendship_ack(request: Request) -> JSONResponse:
    """Reciprocal friendship handshake (2026-07-29) — the redeemer's callback
    after importing our token, so we complete a BIDIRECTIONAL pairing in one
    round trip instead of requiring a second, independent token exchange in
    reverse. HMAC-authenticated against the pending record saved at
    token-creation time; delegates to the SAME shared core as the stdlib
    server (a2a_http_server.process_friendship_ack_request) so the two
    backends cannot drift — same invariant as /v1/a2a/ping above.
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
    import os as _os_ack
    from pathlib import Path as _Path_ack
    from a2a_friendship import process_friendship_ack_request  # type: ignore[import-not-found]
    from a2a_http_server import (  # type: ignore[import-not-found]
        _endpoints_dir as _a2a_endpoints_dir,
        _pending_friendships_dir as _a2a_pending_friendships_dir,
    )
    _origins_env = _os_ack.environ.get("REMOTE_ORIGINS_DIR")
    origins_dir_path = (
        _Path_ack(_origins_env) if _origins_env
        else _a2a_shared.parent.parent / "cowork" / "remote_origins"
    )
    # A5 (2026-07-30 relay redesign): run the fully-sync ack handler (flock,
    # getaddrinfo, 5 s urllib ping) off the event loop so one ack can't freeze
    # the gateway. Same fix as corvin_console.standalone's ack route.
    import asyncio as _asyncio_ack
    status_code, payload = await _asyncio_ack.to_thread(
        process_friendship_ack_request,
        body,
        pending_dir=_a2a_pending_friendships_dir(),
        origins_dir=origins_dir_path,
        endpoints_dir=_a2a_endpoints_dir(),
    )
    return JSONResponse(content=payload, status_code=status_code)


# ── Auth note ────────────────────────────────────────────────────────
#
# Static atlr_* token auth has been removed. For local deployments the
# loopback binding (127.0.0.1) is the security boundary — and ``_jwt_guard``
# now ENFORCES it: a non-loopback peer must present a valid OIDC JWT
# (E-09). Run the gateway with ``--no-proxy-headers`` unless a trusted
# reverse proxy stamps ``X-Forwarded-For`` for every request; with proxy
# headers on, a proxy that omits the header makes remote callers look
# loopback (the same hazard as the console's local-login, see
# ``corvin_console.standalone``).


# ── Endpoints ────────────────────────────────────────────────────────


def _headless_ui() -> bool:
    """True when this process must serve no browser surface (ADR-0241/0243).

    Read per request rather than captured at import time: the operator flips
    ``headless_api_mode`` from Settings → Features without a restart, and a
    cached answer would keep the redirect targets pointing at a SPA that is no
    longer mounted.

    Defensive by contract — every failure path returns False, i.e. "serve the UI
    exactly as before the flag existed".
    """
    try:
        import sys as _s
        from pathlib import Path as _P

        _cp = _P(__file__).resolve().parents[2] / "console"
        if str(_cp) not in _s.path:
            _s.path.insert(0, str(_cp))
        from corvin_console.app import headless_enabled  # type: ignore[import-not-found]

        return bool(headless_enabled())
    except Exception:  # noqa: BLE001 — no console package means "serve as usual"
        return False


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Hand a browser the SPA's icon — unless there is no SPA.

    Same class as ``GET /``: in headless mode ``/console/`` is not mounted, so
    this 302 pointed into a 404. A browser surface that answers "look over
    there" at something that does not exist is still a browser surface.
    """
    from starlette.responses import RedirectResponse

    if _headless_ui():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "headless-no-browser-surface"},
        )
    return RedirectResponse(url="/console/favicon.svg", status_code=302)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Send a browser to the console — unless there is no console to send it to.

    In headless mode (ADR-0241/0243) ``/console/`` is not mounted, so the
    redirect would hand every visitor a 302 into a 404. Answering with the
    liveness shape instead keeps the root useful for the API-only deployment
    this mode exists for.
    """
    from starlette.responses import RedirectResponse

    if _headless_ui():
        return {"status": "ok", "version": __version__, "ui": "headless"}
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
