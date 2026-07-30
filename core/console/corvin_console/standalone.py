"""Standalone FastAPI application for native (non-Docker) deployments.

Usage
-----
Start directly::

    uvicorn corvin_console.standalone:create_app --factory \
        --host 0.0.0.0 --port 8000 \
        --ws-ping-interval 20 --ws-ping-timeout 30

Or simply::

    python -m corvin_console.standalone

The ``--ws-ping-interval`` flag enables protocol-level WebSocket pings (RFC 6455
opcode 0x9) every 20 s. These keep connections alive through proxies that drop idle
sockets after 60 s, and work even during long tool calls when no data frames flow.

Or via ``corvin serve`` / ``corvin start`` (pip-install path).

The app exposes:
  /v1/console/...   Console REST API (all existing routes)
  /console/         React SPA (served from web-next/dist/)
  /local-stats      Local stats HTML dashboard
  /                 Redirect → /v1/console/auth/local-login

local-login creates a session automatically for localhost operators and
redirects to /console/. The SetupGate component then guides first-time
configuration (engine key, optional bridge channel).

Headless API-only mode (ADR-0241/0243, feature flag ``headless_api_mode``,
default off) removes every browser surface from THIS app: no /console/ mount,
no /local-stats, and / answers ``{"status": "ok", "ui": "headless"}`` instead of
redirecting into a login hop that ends at a SPA which is not there. The REST API
is untouched — the mode is API-only, not off.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .app import mount_static, router

_LOCAL_STATS_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
 <meta charset="UTF-8">
 <meta name="viewport" content="width=device-width,initial-scale=1">
 <title>CorvinOS — Lokale Stats</title>
 <style>
  *, *::before, *::after { box-sizing: border-box; }
  :root { --bg:#f9f7f4; --bg-card:#fff; --border:#e5e0d8; --text:#2a2420; --muted:#7a7066;
          --amber:#e8a83a; --green:#22c55e; --green-dim:rgba(34,197,94,.08); }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:var(--bg); color:var(--text); }
  .hero { max-width:900px; margin:0 auto; padding:3rem 1.5rem 1.5rem; }
  .hero h1 { font-size:clamp(1.6rem,3vw,2.4rem); margin:0 0 .5rem;
              font-family:Georgia,serif; display:flex; align-items:center; gap:.5rem; }
  .hero p  { color:var(--muted); margin:0; }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--green);
         display:inline-block; animation:pulse 2s ease-in-out infinite; }
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
          gap:1rem; max-width:900px; margin:1.5rem auto; padding:0 1.5rem; }
  .card { background:var(--bg-card); border:1px solid var(--border); border-radius:10px;
          padding:1.25rem 1rem; text-align:center; }
  .card.green { border-color:rgba(34,197,94,.3); background:var(--green-dim); }
  .val { font-size:2rem; font-weight:800; color:var(--amber); line-height:1;
         margin-bottom:.3rem; font-variant-numeric:tabular-nums; }
  .card.green .val { color:var(--green); }
  .lbl { font-size:.82rem; color:var(--muted); font-weight:600; }
  .sub { font-size:.72rem; color:var(--muted); margin-top:.2rem; opacity:.75; }
  .info { max-width:900px; margin:0 auto 2rem; padding:0 1.5rem;
          display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:.75rem; }
  .row  { background:var(--bg-card); border:1px solid var(--border); border-radius:8px;
          padding:.75rem 1rem; display:flex; justify-content:space-between; align-items:center; }
  .row-k { font-size:.82rem; color:var(--muted); }
  .row-v { font-size:.82rem; font-weight:600; color:var(--text); font-family:monospace; }
  .badge { display:inline-block; padding:.15em .5em; border-radius:4px; font-size:.72rem;
           font-weight:700; }
  .badge.on  { background:rgba(34,197,94,.15); color:#15803d; }
  .badge.off { background:rgba(239,68,68,.1);  color:#b91c1c; }
  .ts { text-align:right; font-size:.72rem; color:var(--muted); padding:0 1.5rem .5rem; max-width:900px; margin:0 auto; }
  .err { background:#fef2f2; border:1px solid #fecaca; border-radius:8px;
         padding:.65rem 1rem; font-size:.82rem; color:#991b1b;
         max-width:900px; margin:1rem auto; padding-left:1.5rem; display:none; }
  @media(max-width:500px){.val{font-size:1.6rem}}
 </style>
</head>
<body>
 <div class="hero">
  <h1><span class="dot"></span>CorvinOS — Lokale Stats</h1>
  <p>Diese Instanz live, aus lokalen Daten — kein externes API nötig.</p>
 </div>
 <div class="err" id="err">Fehler beim Laden — Console läuft?</div>
 <div class="grid" id="tiles">
  <div class="card green"><div class="val" id="t-uptime">—</div><div class="lbl">Uptime</div></div>
  <div class="card"><div class="val" id="t-version">—</div><div class="lbl">Version</div></div>
  <div class="card"><div class="val" id="t-engine">—</div><div class="lbl">Engine</div></div>
  <div class="card"><div class="val" id="t-sessions">—</div><div class="lbl">Aktive Sessions</div><div class="sub">letzte 5 Min</div></div>
 </div>
 <div class="info" id="info">
  <div class="row"><span class="row-k">Plattform</span><span class="row-v" id="i-platform">—</span></div>
  <div class="row"><span class="row-k">Python</span><span class="row-v" id="i-python">—</span></div>
  <div class="row"><span class="row-k">Instance ID</span><span class="row-v" id="i-iid">—</span></div>
  <div class="row"><span class="row-k">Ping (Telemetrie)</span><span id="i-ping">—</span></div>
  <div class="row"><span class="row-k">Heartbeat-Thread</span><span id="i-hb">—</span></div>
 </div>
 <div class="ts" id="ts"></div>

 <script>
 (function(){
  function load(){
   fetch('/v1/console/local-stats',{credentials:'include'})
    .then(function(r){if(!r.ok)throw new Error(r.status);return r.json();})
    .then(function(d){
     document.getElementById('err').style.display='none';
     document.getElementById('t-uptime').textContent  = d.uptime_label||'—';
     document.getElementById('t-version').textContent = d.version||'—';
     document.getElementById('t-engine').textContent  = (d.engine||'—').replace('_',' ');
     var s=d.active_sessions; document.getElementById('t-sessions').textContent=s>=0?s:'?';
     document.getElementById('i-platform').textContent = d.platform||'—';
     document.getElementById('i-python').textContent   = d.python||'—';
     document.getElementById('i-iid').textContent      = d.instance_id||'—';
     function badge(v,y,n){return '<span class="badge '+(v?'on':'off')+'">'+(v?y:n)+'</span>';}
     document.getElementById('i-ping').innerHTML = badge(d.ping_enabled,'Aktiv','Deaktiviert');
     document.getElementById('i-hb').innerHTML   = badge(d.heartbeat_alive,'Läuft','Gestoppt');
     document.getElementById('ts').textContent   = 'Aktualisiert: '+d.sampled_at;
    })
    .catch(function(){document.getElementById('err').style.display='block';});
  }
  load();
  setInterval(load,30000);
 })();
 </script>
</body>
</html>"""

log = logging.getLogger(__name__)

# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return the standalone CorvinOS console application.

    Callable as a uvicorn ``--factory`` target:
    ``corvin_console.standalone:create_app``
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(application: FastAPI):  # noqa: ARG001
        # Activate the installed license in THIS process. The adapter loads it
        # at boot (adapter.py), but the standalone console process did not — so
        # a valid <corvin_home>/global/license.key (or CORVIN_LICENSE_KEY) was
        # ignored and the console reported `free` regardless of the customer's
        # tier (paid features stayed gated). load_license_from_env() is
        # idempotent + best-effort (absence simply leaves the free-tier fallback).
        try:
            from license.validator import load_license_from_env as _lic_load
            _lic_load()
        except Exception:
            pass

        # ── ADR-0232/0233 — compliance tripwires + plugins (FAIL-CLOSED) ─────
        # Deliberately NOT wrapped in `except: pass` like the best-effort blocks
        # around it. This app is what `corvinos-serve` runs and what install.sh
        # launches, and until this call existed it was the one shipped entry
        # point that served requests without ever asking whether the GDPR
        # Art. 30/32 audit chain verifies — a console with a corrupted chain
        # booted happily while the tripwire, called by hand in the same process,
        # refused. The sequence is shared with corvin_gateway.app so the two
        # hosts cannot drift; see corvin_plugins.bootstrap.boot_platform.
        #
        # An ABSENT plugin package (stripped install) is not a failure and must
        # not stop the boot — only a broken mechanism is. A present-but-failing
        # tripwire propagates: there is no override, by design.
        _plugins_loaded: list[str] = []
        try:
            from corvin_plugins.bootstrap import boot_platform as _boot_platform
        except ImportError:
            _boot_platform = None  # type: ignore[assignment]
            log.debug("corvin_plugins absent — compliance tripwires not available")
        if _boot_platform is not None:
            try:
                _plugins_loaded = _boot_platform()  # raises -> boot aborts
            except Exception as e:
                # Fresh install robustness: provide actionable error message
                log.error(
                    "Platform bootstrap failed: %s (%s). "
                    "This may be a fresh install or corrupted configuration. "
                    "Try: rm -rf ~/.corvin && corvin start",
                    e, type(e).__name__
                )
                raise

        # ── Phase 1a: Voice config migration (best-effort — never blocks startup) ─
        # Auto-migrate voice configuration from legacy ~/.config/corvin-voice/
        # to tenant-scoped <corvin_home>/tenants/<tenant_id>/voice/ on first access.
        # This is idempotent and transparent.
        try:
            from .voice_config import get_voice_config_manager
            mgr = get_voice_config_manager()
            if mgr.needs_migration():
                result = mgr.migrate_from_legacy()
                if result.success and result.migrated_items > 0:
                    log.info(
                        f"Voice config migrated: {result.migrated_items} items "
                        f"from {mgr.legacy_voice_config_dir()} to {mgr.voice_home()}"
                    )
                elif not result.success:
                    log.warning(
                        f"Voice migration had errors: {'; '.join(result.errors)}"
                    )
        except Exception as e:
            log.debug(f"Voice config initialization (non-blocking): {e}")

        # Start presence heartbeat (best-effort — never blocks startup).
        try:
            from .aco.heartbeat import start_heartbeat_thread as _start_hb
            import forge.paths as _fp  # type: ignore[import]
            _start_hb(_fp.corvin_home())
        except Exception:
            pass

        # ADR-0258 Stage 3 — A2A relay listener (best-effort, never blocks
        # startup). Inert unless BOTH the feature flag is on AND a relay URL
        # is configured — the common case (flag off) does nothing here at
        # all, matching every other ship-dark flag in this file.
        # `_a2a_receiver`/`_a2a_available` are assigned later in create_app()
        # (below, at the A2A route-wiring block) — valid: this closure is
        # only CALLED by uvicorn after create_app() has fully returned, and
        # Python resolves free variables in the enclosing scope by name at
        # call time, not at definition time.
        _relay_listener = None
        _relay_task = None
        try:
            from corvin_console import feature_flags as _relay_ff
            import a2a_friendship as _relay_ft  # type: ignore[import-not-found]
            if _a2a_available and _a2a_receiver is not None and _relay_ff.is_enabled("a2a_relay_fallback"):
                _relay_url = _relay_ft.get_my_relay_url()
                if _relay_url:
                    import asyncio as _relay_asyncio
                    import a2a_relay as _relay_mod  # type: ignore[import-not-found]
                    from .routes.a2a_pair import _origins_dir as _relay_origins_dir
                    _relay_listener = _relay_mod.RelayListener(
                        relay_url=_relay_url, receiver=_a2a_receiver,
                        origins_dir=_relay_origins_dir(),
                    )
                    _relay_task = _relay_asyncio.create_task(_relay_listener.run_forever())
                    log.info("A2A relay listener started: %s", _relay_url)
        except Exception:
            log.exception("A2A relay listener failed to start (non-fatal)")

        yield
        # Detach provider slots so a draining request cannot be routed into a
        # half-torn-down plugin. Best-effort: shutdown must not raise.
        if _plugins_loaded:
            try:
                from corvin_plugins.bootstrap import shutdown as _plugin_shutdown
                _plugin_shutdown(_plugins_loaded)
            except Exception:
                pass
        if _relay_listener is not None:
            _relay_listener.stop()
        if _relay_task is not None:
            _relay_task.cancel()

    app = FastAPI(
        title="CorvinOS Console",
        version="1.0",
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,
        lifespan=_lifespan,
    )

    # Reject an oversized upload on its Content-Length, BEFORE Starlette parses
    # the multipart body. /voice/transcribe declares a 25 MiB cap but enforced it
    # with `if len(await audio.read()) > _MAX_AUDIO_BYTES` — after the entire body
    # was spooled to disk AND materialised in RAM, so a 2 GB POST cost 2 GB of
    # each before the 413 fired. The console is single-process, so that stalls
    # every SSE chat stream with it.
    #
    # Deliberately PER-PATH rather than a global body cap: the file-attachment
    # routes take legitimately large uploads and a console-wide limit would be a
    # behaviour change well outside this fix. Paths not listed are untouched.
    _BODY_CAPS = {"/v1/console/voice/transcribe": 25 * 1024 * 1024}

    @app.middleware("http")
    async def _cap_request_body(request, call_next):  # noqa: ANN001, ANN202
        cap = _BODY_CAPS.get(request.url.path)
        if cap is not None:
            raw_len = request.headers.get("content-length")
            if raw_len:
                try:
                    if int(raw_len) > cap:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": f"audio exceeds {cap} bytes"},
                        )
                except ValueError:
                    pass  # unparseable — let the handler's own check deal with it
        return await call_next(request)

    # Allow the same-origin SPA to call the API in development.
    # In production (serving SPA from the same origin) this is a no-op.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount all console API routes at /v1/console
    app.include_router(router, prefix="/v1/console")

    # ── Layer 38 — A2A inbound receive + ping (ADR-0048 / ADR-0199) ─────────
    # `corvin_console.standalone` is what `corvinos-serve` runs (see the module
    # docstring) — the DEFAULT process on every install.ps1/install.sh autostart
    # path. Until this wiring existed, this app had NO listener for incoming A2A
    # calls at all: `corvin_gateway.app` already wires these same two routes, but
    # nothing starts the gateway by default — so two paired `corvinos-serve`
    # instances could never reach each other inbound, no matter how correct the
    # pairing/token exchange was (found 2026-07-29 debugging a "friendship shows
    # connected but is unreachable" report). Mirrors corvin_gateway.app's
    # /v1/a2a/receive and /v1/a2a/ping wiring EXACTLY — same shared receiver
    # module, same CORVIN_A2A_ENGINE selector, same process_ping_request core —
    # so the two hosts cannot drift. Mounted at the app ROOT (not under
    # /v1/console) and outside require_session/require_csrf: the caller is a
    # remote peer instance authenticated via its own HMAC pairing keys, not a
    # logged-in browser session — matching the wire path RemoteEndpointRegistry
    # stores (".../v1/a2a/receive").
    try:
        from remote_trigger_receiver import (  # type: ignore[import-not-found]
            RemoteTriggerReceiver as _RemoteTriggerReceiver,
        )

        _a2a_engine_name = os.environ.get("CORVIN_A2A_ENGINE", "claude").strip().lower()
        _a2a_engine_factory = None
        if _a2a_engine_name == "compute":
            from a2a_compute_engine import (  # type: ignore[import-not-found]
                DeterministicComputeEngine as _DCE,
            )
            _a2a_engine_factory = lambda: _DCE()  # noqa: E731

        _a2a_receiver = _RemoteTriggerReceiver(engine_factory=_a2a_engine_factory)
        _a2a_available = True
    except Exception:
        log.exception("A2A receiver unavailable — /v1/a2a/receive and /v1/a2a/ping will 503")
        _a2a_available = False
        _a2a_receiver = None

    @app.post("/v1/a2a/receive", include_in_schema=False)
    async def _a2a_receive(request: Request) -> JSONResponse:
        """Layer 38 — A2A inbound receive.

        HMAC-authenticated (no bearer token, no session cookie). Validates the
        signed TaskEnvelope, anchors the exchange in the L16 audit chain, and
        returns a signed ResponseEnvelope. ADR-0048.
        """
        if not _a2a_available or _a2a_receiver is None:
            raise HTTPException(status_code=503, detail={"reason": "a2a_not_configured"})
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"reason": "invalid_json"})
        response = _a2a_receiver.receive(body)
        return JSONResponse(content=response.to_dict())

    @app.post("/v1/a2a/ping", include_in_schema=False)
    async def _a2a_ping(request: Request) -> JSONResponse:
        """ADR-0199 — lightweight peer-liveness check (receiver side).

        HMAC-authenticated signed probe; responds with a recv_key-signed body
        echoing task_id=ping_id. Delegates to the SAME shared core as the
        stdlib server (a2a_http_server.process_ping_request) so the backends
        cannot drift.
        """
        if not _a2a_available or _a2a_receiver is None:
            raise HTTPException(status_code=503, detail={"reason": "a2a_not_configured"})
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"reason": "invalid_json"})
        from a2a_http_server import process_ping_request  # type: ignore[import-not-found]
        status_code, payload = process_ping_request(body, _a2a_receiver)
        return JSONResponse(content=payload, status_code=status_code)

    @app.post("/v1/a2a/friendship-ack", include_in_schema=False)
    async def _a2a_friendship_ack(request: Request) -> JSONResponse:
        """Reciprocal friendship handshake (2026-07-29) — the redeemer's
        callback after importing a token, so the issuer completes a
        BIDIRECTIONAL pairing in one round trip instead of requiring a
        second, independent token exchange in reverse. HMAC-authenticated
        against the pending record saved at token-creation time (see
        a2a_friendship.save_pending_friendship /
        process_friendship_ack_request) — no session cookie, no bearer
        token."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"reason": "invalid_json"})
        from a2a_friendship import process_friendship_ack_request  # type: ignore[import-not-found]
        # Reuse the SAME dir resolvers the pairing routes already use (env-var
        # override else repo-relative default) rather than re-deriving the
        # path here — a second, independently-computed default would silently
        # diverge from where friendship_create/friendship_import actually
        # read and write on a wheel install.
        from .routes.a2a_pair import (
            _endpoints_dir as _a2a_endpoints_dir,
            _origins_dir as _a2a_origins_dir,
            _pending_friendships_dir as _a2a_pending_friendships_dir,
        )
        # A5 (2026-07-30 relay redesign): process_friendship_ack_request is
        # fully SYNC and blocks on fcntl.flock, socket.getaddrinfo (no timeout,
        # on a peer-supplied hostname) and a 5 s urllib ping. Called directly in
        # this async handler it froze the ENTIRE console event loop — including
        # the relay-listener background task — for ≥5 s per ack. Run it off the
        # loop (the RelayListener already uses this exact idiom).
        import asyncio as _asyncio
        status_code, payload = await _asyncio.to_thread(
            process_friendship_ack_request,
            body,
            pending_dir=_a2a_pending_friendships_dir(),
            origins_dir=_a2a_origins_dir(),
            endpoints_dir=_a2a_endpoints_dir(),
        )
        return JSONResponse(content=payload, status_code=status_code)

    # Mount the pre-built React SPA at /console
    mount_static(app, url_prefix="/console")

    # ADR-0241/0243 — headless API-only mode. This factory, not the gateway, is
    # what `corvin serve` runs (ops/launcher/corvin/serve_backend.py pins
    # `corvin_console.standalone:create_app`), so gating the browser surfaces in
    # the gateway alone left every pip-install serving them. mount_static()
    # already declines /console; /local-stats and / are the OTHER two browser
    # surfaces on this app and have to make the same decision.
    #
    # Defensive: any failure to answer the question means "serve the UI", i.e.
    # exactly the behaviour that existed before the flag.
    headless = False
    try:
        from .app import headless_enabled

        headless = bool(headless_enabled())
    except Exception:  # noqa: BLE001 — unreadable flag means "serve as usual"
        headless = False

    if not headless:
        # Local stats page — no Railway, no remote API, reads only local state.
        # Served at /local-stats (outside the SPA prefix /console so it's a bare page).
        @app.get("/local-stats", include_in_schema=False)
        def _local_stats_page() -> HTMLResponse:
            return HTMLResponse(content=_LOCAL_STATS_HTML)

        # Root redirect → local-login → session cookie → /console/
        @app.get("/", include_in_schema=False)
        def _root() -> RedirectResponse:
            return RedirectResponse("/v1/console/auth/local-login", status_code=302)
    else:
        # The login hop this used to redirect to ends at /console/, which is not
        # mounted here — so the redirect would hand every visitor a 302 into a
        # 404. Answer the liveness shape instead, like the gateway root does.
        @app.get("/", include_in_schema=False)
        def _root_headless() -> JSONResponse:
            return JSONResponse({"status": "ok", "ui": "headless"})

    log.info(
        "CorvinOS standalone app ready — headless=%s, local-login enabled by default",
        headless,
    )
    return app


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=20,
        ws_ping_timeout=30,
    )
