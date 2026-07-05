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
  /                 Redirect → /v1/console/auth/local-login

local-login creates a session automatically for localhost operators and
redirects to /console/. The SetupGate component then guides first-time
configuration (engine key, optional bridge channel).
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

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
        # Start presence heartbeat (best-effort — never blocks startup).
        try:
            from .aco.heartbeat import start_heartbeat_thread as _start_hb
            import forge.paths as _fp  # type: ignore[import]
            _start_hb(_fp.corvin_home())
        except Exception:
            pass
        yield

    app = FastAPI(
        title="CorvinOS Console",
        version="1.0",
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,
        lifespan=_lifespan,
    )

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

    # Mount the pre-built React SPA at /console
    mount_static(app, url_prefix="/console")

    # Local stats page — no Railway, no remote API, reads only local state.
    # Served at /local-stats (outside the SPA prefix /console so it's a bare page).
    @app.get("/local-stats", include_in_schema=False)
    def _local_stats_page() -> HTMLResponse:
        return HTMLResponse(content=_LOCAL_STATS_HTML)

    # Root redirect → local-login → session cookie → /console/
    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse("/v1/console/auth/local-login", status_code=302)

    log.info("CorvinOS standalone app ready — local-login enabled by default")
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
