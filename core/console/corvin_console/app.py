"""FastAPI router for the console-UI plugin.

Exposes a single ``router: APIRouter`` that the gateway's ``app.py``
mounts at ``/v1/console``. Single-operator deployments that never
bootstrap this plugin see an ``ImportError`` on the gateway side and
the include is silently skipped.

Phase A — Auth + Dashboard. Phase B+ adds Sessions, Runs, Personas,
Tools, Skills, Memory, Compute, Workspaces, Audit-Explorer, Settings,
Members.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as _StarletteHTTPException


class _SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for unknown paths.

    This makes React Router's BrowserRouter work on hard refresh and
    direct URL access: any path under /console/ that does not match a
    real asset (JS chunk, CSS, image) is served as index.html so the
    SPA can handle the route client-side.
    """

    async def get_response(self, path: str, scope: dict):  # type: ignore[override]
        try:
            response = await super().get_response(path, scope)
        except _StarletteHTTPException as exc:
            if exc.status_code == 404 and path != "index.html":
                # Let the SPA handle the route
                response = await super().get_response("index.html", scope)
                response.headers["Cache-Control"] = "no-cache"
                return response
            raise
        # Cache policy by asset type. Hashed assets under assets/ are
        # immutable by name and may cache forever. EVERYTHING ELSE served as
        # HTML is the SPA shell and MUST be revalidated on every load —
        # otherwise browsers heuristically cache it (ETag/Last-Modified only)
        # and keep referencing OLD hashed bundles after a deploy, which 404
        # and leave the app stuck on a perpetual "Loading…" screen.
        #
        # The HTML check is keyed on content-type rather than the path string
        # so it also covers the bare directory index ("/console/", path ""
        # or ".") — the most common entry point — which is NOT named
        # index.html and therefore slipped through the old path-based check.
        ctype = response.headers.get("content-type", "")
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif ctype.startswith("text/html") or response.status_code == 304:
            # 304 Not-Modified responses carry no Content-Type so the text/html
            # branch never fires — guard by status_code so the browser still
            # revalidates the SPA shell on every load after a redeploy.
            response.headers["Cache-Control"] = "no-cache"
        return response

from . import __version__
from .routes import (
    auth_routes, dashboard, sessions, audit_tail, runs, personas,
    tasks as tasks_route,
    tools, skills, memory, streams, promote,
    workspaces, members, compute, settings as settings_route,
    profile as profile_route, chat_settings as chat_settings_route,
    landing as landing_route,
    bridges as bridges_route,
    ldd as ldd_route,
    quality_layers as quality_layers_route,
    chat as chat_route,
    voice as voice_route,
    workflows as workflows_route,
    connectors as connectors_route,
    setup as setup_route,
    settings_stream as settings_stream_route,
    byok as byok_route,
    engine as engine_route,
    engine_pref as engine_pref_route,
    remote_trigger_log as remote_trigger_log_route,
    a2a_pair as a2a_pair_route,
    files as files_route,
    space as space_route,
    grants as grants_route,
    orgs as orgs_route,
    tokens as tokens_route,
    assistant as assistant_route,
    license as license_route,
    instance as instance_route,
    rag as rag_route,
    rag_hub as rag_hub_route,
    rag_hub_analytics as rag_hub_analytics_route,
    custom_provider as custom_provider_route,
    mcp_plugins as mcp_plugins_route,
    data_sources as data_sources_route,
    chain_dual_track as chain_dual_track_route,
    flows as flows_route,
    # ADR-0124 — Open Platform Extensibility (M1–M7)
    custom_engines as custom_engines_route,
    connectors_custom as connectors_custom_route,
    compute_jobs as compute_jobs_route,
    datasources_http as datasources_http_route,
    skills_manual as skills_manual_route,
    tools_manual as tools_manual_route,
    audit_layers as audit_layers_route,
    webhooks as webhooks_route,
    # ADR-0131 — Agent Lifecycle Governance
    agents as agents_route,
    # ADR-0142 — Layer Extension API
    extensions as extensions_route,
    # UAH — Universal Activity Hub (Chat as Kommandozentrale)
    activity as activity_route,
    # ADR-0163 — User-Defined Learning Objectives
    ulo as ulo_route,
    # ADR-0174 — Autonomous Chat Observatory (ACO)
    aco as aco_route,
    # ADR-0178 / ADR-0180 — Self-healing config (ACO L5 toggles + healing telemetry)
    healing_config as healing_config_route,
    # ADR-0182 — Browser automation (agent-driven browser + live view)
    browser as browser_route,
    # Local instance stats (no remote API)
    local_stats as local_stats_route,
)


router = APIRouter()
router.include_router(auth_routes.router, prefix="/auth", tags=["console-auth"])
router.include_router(dashboard.router, tags=["console-dashboard"])
# Phase B — read-only viewers
router.include_router(sessions.router, tags=["console-sessions"])
router.include_router(audit_tail.router, tags=["console-audit"])
router.include_router(chain_dual_track_route.router, tags=["console-audit"])
router.include_router(remote_trigger_log_route.router, tags=["console-a2a"])
router.include_router(a2a_pair_route.router, tags=["console-a2a-pair"])
router.include_router(runs.router, tags=["console-runs"])
router.include_router(tasks_route.router, tags=["console-tasks"])
router.include_router(personas.router, tags=["console-personas"])
# Phase C — drilldowns
# ADR-0124 M5a/M5b: manual-skill and manual-tool routers MUST be registered
# before the generic skills/tools routers so that /skills/manual and
# /tools/manual are not captured by the wildcard /{name} routes in
# skills.py and tools.py.
router.include_router(skills_manual_route.router, tags=["console-skills-manual"])
router.include_router(tools_manual_route.router, tags=["console-tools-manual"])
router.include_router(tools.router, tags=["console-tools"])
router.include_router(skills.router, tags=["console-skills"])
router.include_router(memory.router, tags=["console-memory"])
# Phase D — realtime SSE streams
router.include_router(streams.router, tags=["console-streams"])
# Phase E — mutations (promote endpoints; memory + persona writes
# live inside their own routers above, gated by require_csrf +
# verify_reauth).
router.include_router(promote.router, tags=["console-promote"])
# Phase F — read-only viewers for the remaining four sections
router.include_router(workspaces.router,    tags=["console-workspaces"])
router.include_router(members.router,       tags=["console-members"])
router.include_router(compute.router,       tags=["console-compute"])
# ADR-0067: engine routers MUST be registered before settings_route.
# settings_route has PUT /settings/{label} (parameterised); Starlette matches
# routes in registration order, so PUT /settings/engine would be swallowed by
# the wildcard and validated against SettingsWriteRequest — producing
# "Field required + 5x Extra inputs are not permitted".
router.include_router(engine_route.router, tags=["console-engine"])
router.include_router(engine_pref_route.router, tags=["console-engine-pref"])
router.include_router(settings_route.router, tags=["console-settings"])
# Phase G — user-profile + chat-settings tab
router.include_router(profile_route.router,      tags=["console-profile"])
router.include_router(chat_settings_route.router, tags=["console-chat-settings"])
# ADR-0037 (web-next) — public landing endpoints (unauthenticated).
router.include_router(landing_route.router, prefix="/landing", tags=["console-landing"])
# ADR-0124 M7 — Generic Webhook Bridge
router.include_router(webhooks_route.router, tags=["console-webhooks"])
# ADR-0037 (web-next) — bridges settings editor (Iter 2b).
router.include_router(bridges_route.router, tags=["console-bridges"])
# ADR-0037 (web-next) — LDD layer toggles (Iter 2e).
router.include_router(ldd_route.router, tags=["console-ldd"])
# Quality Layers (ADR Gate, docs-as-definition-of-done, etc.) toggles.
router.include_router(quality_layers_route.router, tags=["console-quality-layers"])
# ADR-0037 (web-next) — web-bridge chat + voice (Iter 3a/b).
router.include_router(chat_route.router, tags=["console-chat"])
router.include_router(voice_route.router, tags=["console-voice"])
# ADR-0039 — Workflow Builder (Phases 1-3).
router.include_router(workflows_route.router, tags=["console-workflows"])
router.include_router(connectors_route.router, tags=["console-connectors"])
router.include_router(setup_route.router, tags=["console-setup"])
# Phase D extension — settings file watcher SSE stream.
router.include_router(settings_stream_route.router, tags=["console-settings-stream"])
# ADR-0047 — BYOK key management (hosted-mode + self-hosted).
router.include_router(byok_route.router, tags=["console-byok"])
# File Hub — browse, upload, download, delete tenant files.
router.include_router(files_route.router, tags=["console-files"])
# Layer 40 — CorvinSpace personal profile + public domains.
router.include_router(space_route.router, prefix="/space", tags=["console-space"])
# Layer 41 — Social Capability Grants (personal actor).
router.include_router(grants_route.router, prefix="/grants", tags=["console-grants"])
# Layer 42 — CorvinOrg organisation actors.
router.include_router(orgs_route.router, prefix="/orgs", tags=["console-orgs"])
# Token management router kept as empty stub (tokens removed — see routes/tokens.py).
# ADR-0062 — Console floating assistant (stateless claude -p wrapper).
router.include_router(assistant_route.router, tags=["console-assistant"])
# Phase 4 — RAG Integration (retrieval-augmented generation).
router.include_router(rag_route.router, tags=["console-rag"])
# Phase 7 — RAG Hub (provider marketplace).
router.include_router(rag_hub_route.router, tags=["console-rag-hub"])
router.include_router(rag_hub_analytics_route.router, tags=["console-rag-hub-analytics"])
# Phase 8 — Custom Provider Setup (Web-Integrated).
router.include_router(custom_provider_route.router, tags=["console-custom-provider"])
# ADR-0017 Phase IV — License management (upload, revoke, status, audit).
router.include_router(license_route.router, tags=["console-license"])
router.include_router(instance_route.router, tags=["console-instance"])
# ADR-0096 M3 — MCP Plugin Manager console UI.
router.include_router(mcp_plugins_route.router, tags=["console-mcp-plugins"])
# ADR-0106 — DSI v1 Data Source management.
router.include_router(data_sources_route.router, tags=["console-data-sources"])
# ADR-0121 — CorvinFlow FlowRun timeline viewer + checkpoint approve.
router.include_router(flows_route.router, tags=["console-flows"])
# ADR-0124 M1 — Custom Engine Registration
router.include_router(custom_engines_route.router, tags=["console-custom-engines"])
# ADR-0124 M2 — Custom Connector Registry
router.include_router(connectors_custom_route.router, tags=["console-connectors-custom"])
# ADR-0124 M3 — Compute Job Creator
router.include_router(compute_jobs_route.router, tags=["console-compute-jobs"])
# ADR-0124 M4 — DSI v2 HTTP Adapter Registry
router.include_router(datasources_http_route.router, tags=["console-datasources-http"])
# ADR-0124 M6 — Custom Audit Layers
router.include_router(audit_layers_route.router, tags=["console-audit-layers"])
# ADR-0131 — Agent Lifecycle Governance
router.include_router(agents_route.router, tags=["console-agents"])
# ADR-0142 — Layer Extension API
router.include_router(extensions_route.router, tags=["console-extensions"])
# UAH — Universal Activity Hub (Chat as Kommandozentrale)
router.include_router(activity_route.router, tags=["console-activity"])
# ADR-0163 M4 — User-Defined Learning Objectives API
router.include_router(ulo_route.router, tags=["console-ulo"])
# ADR-0174 — Autonomous Chat Observatory (ACO) — anomaly scan, diagnosis, replay
router.include_router(aco_route.router, tags=["console-aco"])
# ADR-0178 / ADR-0180 — Self-healing config (ACO L5 toggles + healing telemetry)
router.include_router(healing_config_route.router, tags=["console-healing-config"])
router.include_router(browser_route.router, tags=["console-browser"])
router.include_router(local_stats_route.router, tags=["console-local-stats"])


@router.get("/version")
def version() -> dict[str, str]:
    """Plugin version. Unauthenticated by design."""
    return {"version": __version__}


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — unauthenticated.

    Returns a minimal payload that a load-balancer / systemd-unit
    can poll: ``{ok: true, version, plugin: "corvin-console"}``.
    No tenant state is consulted; this is a "is the router mounted"
    probe, not a "is the tenant configured" probe.
    """
    return {"ok": True, "version": __version__, "plugin": "corvin-console"}


# ── Static frontend mount ─────────────────────────────────────────────
#
# The console SPA is the Vite + React + Tailwind + shadcn build under
# web-next/dist/ (ADR-0037). It must be built with `npm run build` —
# the console bootstrap and the Docker image both do this automatically.
# When the build artifact is absent the SPA is simply not mounted; the
# REST API under /v1/console stays available regardless.


_PKG_DIR = Path(__file__).resolve().parent
_NEXT_DIST_DIR = _PKG_DIR / "web-next" / "dist"


def mount_static(app: FastAPI, *, url_prefix: str = "/console") -> None:
    """Mount the web-next console SPA at ``url_prefix``.

    Requires ``npm run build`` in ``web-next/`` to have produced
    ``dist/``; when that artifact is missing the mount is skipped and a
    clear warning is printed so operators know why the UI is unavailable.
    """
    if not _NEXT_DIST_DIR.exists() or not (_NEXT_DIST_DIR / "index.html").exists():
        import logging
        logging.getLogger(__name__).warning(
            "Console SPA not built at %s — browser UI will return 404. "
            "Fix: cd %s && npm install && npm run build, then restart the gateway.",
            _NEXT_DIST_DIR,
            _NEXT_DIST_DIR.parent,
        )
        # Register a fallback route so the user sees a helpful message instead
        # of a bare FastAPI 404 when the SPA hasn't been built yet.
        from fastapi.responses import HTMLResponse as _HTMLResponse

        @app.get(f"{url_prefix}", include_in_schema=False)
        @app.get(f"{url_prefix}/{{path:path}}", include_in_schema=False)
        async def _spa_not_built(path: str = "") -> _HTMLResponse:
            return _HTMLResponse(
                content=(
                    "<html><body style='font-family:sans-serif;padding:2em'>"
                    "<h2>CorvinOS Console — frontend not built</h2>"
                    "<p>The React SPA hasn't been compiled yet. Run:</p>"
                    f"<pre>cd {_NEXT_DIST_DIR.parent}\nnpm install\nnpm run build</pre>"
                    "<p>Then restart the gateway (<code>corvin-install</code> does this automatically).</p>"
                    "</body></html>"
                ),
                status_code=503,
            )
        return
    app.mount(
        url_prefix,
        _SPAStaticFiles(directory=str(_NEXT_DIST_DIR), html=True),
        name="corvin_console_static",
    )
