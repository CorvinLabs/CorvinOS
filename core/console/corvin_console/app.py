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

import sys
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
from .api import feature_status_endpoints, multi_instance_sync
from .routes import (
    auth_routes, dashboard, sessions, audit_tail, runs, personas,
    tasks as tasks_route,
    tools, skills, memory, streams, promote,
    workspaces, members, compute, settings as settings_route,
    features as features_route,
    profile as profile_route, chat_settings as chat_settings_route,
    landing as landing_route,
    bridges as bridges_route,
    ldd as ldd_route,
    quality_layers as quality_layers_route,
    skill_creator_api as skill_creator_route,
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
    plugins as plugins_route,
    marketplace as marketplace_route,
    marketplace_custom_repos as marketplace_custom_repos_route,
    learning as learning_route,
    learning_dashboard as learning_dashboard_route,
    admin as admin_route,
    data_sources as data_sources_route,
    chain_dual_track as chain_dual_track_route,
    flows as flows_route,
    packages as packages_route,
    # GitHub Cross-Device-Learning Sync (Iteration 1-5)
    github as github_route,
    # ADR-0124 — Open Platform Extensibility (M1–M7)
    custom_engines as custom_engines_route,
    connectors_custom as connectors_custom_route,
    compute_jobs as compute_jobs_route,
    datasources_http as datasources_http_route,
    skills_manual as skills_manual_route,
    skills_monitoring as skills_monitoring_route,
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
    # ADR-0212 — Ecosystem Feature Telemetry
    stats_features as stats_features_route,
    # ADR-0245 — Live Model Discovery
    models as models_route,
    talent as talent_route,
    vibe_engineering as vibe_engineering_route,
    # ADR-0357 P3 — versioned capability manifest (shell renders nav from it)
    capabilities as capabilities_route,
    # ADR-0366 — AI-generated Console panels
    panels as panels_route,
    # ADR-0275/0277 — Multi-Instance Cross-Device Learning
    multi_instance as multi_instance_route,
    # ADR-0400 — Task Graph Visualization (Phase 1-2)
    task_graph_api as task_graph_api_route,
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
router.include_router(skills_monitoring_route.router, tags=["console-skills-monitoring"])
router.include_router(learning_route.router, tags=["console-learning"])
router.include_router(learning_dashboard_route.router, tags=["console-learning-dashboard"])
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
# GitHub Cross-Device-Learning integration
router.include_router(github_route.router, tags=["console-github"])
# MUST precede settings_route: its `PUT /settings/{label}` (config-file writer)
# would otherwise swallow `PUT /settings/worker-engine` as a file label.
router.include_router(features_route.router, tags=["console-settings"])
router.include_router(feature_status_endpoints.router, tags=["console-settings"])
router.include_router(multi_instance_sync.router, tags=["console-settings"])
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
# Skill-Creator (autonomous 6-phase skill builder) — main quality subsystem.
router.include_router(skill_creator_route.router, tags=["console-skill-creator"])
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
# ADR-0233 — plugin registry surface. The routes are always mounted; each one
# 404s while the `plugin_console_surface` flag is off (ships dark), so the gate
# lives in one place instead of in the mount condition.
router.include_router(plugins_route.router, tags=["console-plugins"])
# CONCEPT-0023 — Console Marketplace Panel (Phase 1-2). Discovery, search, install workflow.
router.include_router(marketplace_route.router, tags=["console-marketplace"])
router.include_router(marketplace_custom_repos_route.router,
                      tags=["console-marketplace-custom-repos"])
# ADR-0268 — Skill Package System (marketplace-compatible ZIP distribution).
# packages_route.router already has prefix="/packages", so mount without additional prefix
router.include_router(packages_route.router, tags=["console-packages"])
# ADR-0239/0243 — admin control plane. UI-independent /api/admin/* routes, always
# mounted and each one 404s while the `admin_control_plane` flag is off (ships
# dark), so the gate lives in the route rather than in the mount condition.
router.include_router(admin_route.router, tags=["console-admin"])
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
# ADR-0245 — Live Model Discovery (model registry + live fetch)
router.include_router(models_route.router, tags=["console-models"])
router.include_router(stats_features_route.router, tags=["console-stats"])
router.include_router(talent_route.router, tags=["console-talent"])
router.include_router(vibe_engineering_route.router, tags=["console-vibe-engineering"])
# ADR-0400 — Task Graph Visualization (Phase 1-2 MVP)
router.include_router(task_graph_api_route.router, tags=["console-task-graph"])
router.include_router(capabilities_route.router, tags=["console-capabilities"])
router.include_router(panels_route.router, tags=["console-panels"])
# ADR-0275/0277 — Multi-Instance Cross-Device Learning Dashboard
router.include_router(multi_instance_route.router, tags=["console-multi-instance"])


@router.get("/version")
def version() -> dict[str, str]:
    """Plugin version. Unauthenticated by design."""
    return {"version": __version__}


@router.get("/healthz")
def healthz() -> dict[str, Any]:
    """Liveness probe — unauthenticated (v0.10.60: expanded with installation readiness).

    Returns:
    - `ok: true` if ALL systems are ready (claude + license + adapter)
    - `ok: false` if ANY critical system is degraded
    - `checks` dict with detailed status of each component

    For a minimal liveness check (is the router mounted?), caller can treat
    `response.status_code == 200` as sufficient; the degraded `ok: false` case
    still returns 200 (not 503) because the router itself is working.

    ADR-0215 F1: the import below used to be the bare dotted form
    ``from operator.bridges.shared.engine_detection import ...``, which can
    NEVER resolve — ``operator/`` has no ``__init__.py`` and always loses to
    the stdlib ``operator`` module regardless of sys.path order — so this
    unauthenticated liveness probe raised ``ModuleNotFoundError`` on every
    single call. Fixed by using the repo's established working pattern
    (repo-relative sys.path insert + bare leaf-module import, same as
    ``nerve_builtins._ensure_bridges_on_path``) with the import itself
    guarded, not just the call.
    """
    checks: dict[str, Any] = {
        "console_alive": True,  # We got here, so yes
    }

    # Check 1: Claude credentials
    try:
        _shared_dir = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
        if _shared_dir.is_dir() and str(_shared_dir) not in sys.path:
            sys.path.insert(0, str(_shared_dir))
        from engine_detection import _find_claude_credentials  # type: ignore[import]

        creds_path = _find_claude_credentials()
        checks["claude_authenticated"] = creds_path is not None
    except Exception:  # noqa: BLE001 — liveness probe must never 500
        checks["claude_authenticated"] = False

    # Check 2: License activation (features.json)
    try:
        features_path = Path.home() / ".config" / "corvin-voice" / "features.json"
        checks["license_activated"] = features_path.exists()
    except Exception:  # noqa: BLE001
        checks["license_activated"] = False

    # Check 3: Adapter reachable (adapter must be running for real AI tasks)
    # For now, this is advisory — adapter may not be started yet on fresh installs
    # TODO v0.10.61: poll adapter health endpoint if available

    # Overall: return ok=true only if claude is authenticated
    # (license_activated can be false on free tier; adapter may be starting)
    ok = checks.get("claude_authenticated", False)

    return {
        "ok": ok,
        "version": __version__,
        "plugin": "corvin-console",
        "checks": checks,
    }


# ── Static frontend mount ─────────────────────────────────────────────
#
# The console SPA is the Vite + React + Tailwind + shadcn build under
# web-next/dist/ (ADR-0037). It must be built with `npm run build` —
# the console bootstrap and the Docker image both do this automatically.
# When the build artifact is absent the SPA is simply not mounted; the
# REST API under /v1/console stays available regardless.


_PKG_DIR = Path(__file__).resolve().parent
_NEXT_DIST_DIR = _PKG_DIR / "web-next" / "dist"

HEADLESS_FLAG_ID = "headless_api_mode"


# Per-process headless override (ADR-0352 P2.3b). None = defer to the tenant flag;
# True/False = this process was launched headless (or explicitly with UI). Set by
# the create_app_headless factory that `corvinos run` starts uvicorn with — NOT an
# env var (see headless_enabled's docstring for why that shape was rejected).
_HEADLESS_OVERRIDE: "bool | None" = None


def set_headless_override(value: "bool | None") -> None:
    """Force this PROCESS into (or out of) headless mode, ahead of the tenant flag."""
    global _HEADLESS_OVERRIDE
    _HEADLESS_OVERRIDE = value


def headless_enabled(tenant_id: str | None = None) -> bool:
    """True when this process should serve the API without a browser UI.

    ADR-0241/0243 "API-only" deployment. Reading a *deployment* mode out of a
    per-tenant setting is a deliberate compromise and worth naming: the SPA mount
    is a property of the PROCESS, not of a tenant, so this is resolved once for
    the boot tenant and then applies process-wide. A second tenant on the same
    process does not get its own UI back.

    The alternative — an env var — was rejected because CLAUDE.md requires new
    features to be toggleable from Console → Settings → Features, and an env
    kill-flag is exactly the shape this repo has ruled out elsewhere. If
    per-process configuration ever becomes a real requirement it needs its own
    mechanism, not a second meaning for this setting.

    Every failure mode resolves to False (= today's behaviour, UI mounted).

    Per-PROCESS override (ADR-0352 P2.3b — the "own mechanism" this docstring called
    for): ``corvinos run`` starts uvicorn with the ``create_app_headless`` factory,
    which calls ``set_headless_override(True)`` INSIDE the serving process before the
    app is built. That is not the rejected env-var kill-flag — it is the factory the
    operator explicitly launched — and it takes precedence over the per-tenant setting
    so a headless launch never depends on tenant config being pre-set.

    Phase 1 k=2-5 refactoring: Uses os.headless_mode Skill instead of feature flag.
    """
    if _HEADLESS_OVERRIDE is not None:
        return _HEADLESS_OVERRIDE
    try:
        from core.skills.skill_registry_phase1 import get_registry
    except Exception:  # noqa: BLE001 — no Skills registry means no headless mode
        return False
    try:
        if tenant_id is None:
            from forge.tenants import current_tenant  # type: ignore[import-not-found]

            tenant_id = current_tenant()

        registry = get_registry()
        result = registry.execute("os.headless_mode", {"headless_enabled": False})

        if result.status == "success":
            return bool(result.output.get("headless_enabled", False))
        return False
    except Exception:  # noqa: BLE001 — an unreadable Skill is an off Skill
        import logging

        logging.getLogger(__name__).debug(
            "os.headless_mode could not be read — serving the UI as usual"
        )
        return False


def mount_static(app: FastAPI, *, url_prefix: str = "/console") -> None:
    """Mount the web-next console SPA at ``url_prefix``.

    Requires ``npm run build`` in ``web-next/`` to have produced
    ``dist/``; when that artifact is missing, attempts auto-build.
    If auto-build fails or headless mode is on, serves a helpful error.

    In headless mode (``headless_api_mode``) the SPA is not mounted at all and
    no fallback route is registered — ``/console`` 404s. That is the point of
    the mode: an API-only deployment should not serve a browser surface.
    """
    import logging
    logger = logging.getLogger(__name__)

    if headless_enabled():
        logger.info(
            "%s is on — serving the API without the console SPA", HEADLESS_FLAG_ID
        )
        return

    # Check if SPA is already built
    if (_NEXT_DIST_DIR / "index.html").exists():
        app.mount(
            url_prefix,
            _SPAStaticFiles(directory=str(_NEXT_DIST_DIR), html=True),
            name="corvin_console_static",
        )
        logger.info("Console SPA mounted at %s", url_prefix)
        return

    # SPA not built — attempt auto-build on startup (production hardening)
    logger.warning(
        "Console SPA not found at %s. Attempting auto-build...",
        _NEXT_DIST_DIR,
    )

    import subprocess
    try:
        web_next_dir = _NEXT_DIST_DIR.parent
        subprocess.run(
            ["npm", "install"],
            cwd=web_next_dir,
            check=True,
            capture_output=True,
            timeout=300,  # 5-min timeout for npm install
        )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=web_next_dir,
            check=True,
            capture_output=True,
            timeout=300,  # 5-min timeout for npm run build
        )
        logger.info("Auto-build succeeded. Console SPA is now available.")

        # Try to mount again
        if (_NEXT_DIST_DIR / "index.html").exists():
            app.mount(
                url_prefix,
                _SPAStaticFiles(directory=str(_NEXT_DIST_DIR), html=True),
                name="corvin_console_static",
            )
            return
    except subprocess.CalledProcessError as e:
        logger.error(
            "Auto-build failed: %s. Manual rebuild needed: cd %s && npm install && npm run build",
            e,
            _NEXT_DIST_DIR.parent,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error(
            "Auto-build error (%s): npm may not be installed or build timed out. "
            "Manual rebuild: cd %s && npm install && npm run build",
            type(e).__name__,
            _NEXT_DIST_DIR.parent,
        )

    # Fallback: register helpful error route if build fails
    from fastapi.responses import HTMLResponse as _HTMLResponse

    @app.get(f"{url_prefix}", include_in_schema=False)
    @app.get(f"{url_prefix}/{{path:path}}", include_in_schema=False)
    async def _spa_build_failed(path: str = "") -> _HTMLResponse:
        return _HTMLResponse(
            content=(
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>CorvinOS Console — build failed</h2>"
                "<p>The React SPA couldn't be built automatically.</p>"
                "<p>Manual fix:</p>"
                f"<pre>cd {_NEXT_DIST_DIR.parent}\n"
                "npm install\n"
                "npm run build\n"
                "corvin-install</pre>"
                "</body></html>"
            ),
            status_code=503,
        )


# ✅ PRODUCTION APP FACTORY — exported for uvicorn (all routers built above)
def create_app() -> FastAPI:
    """Create the Console FastAPI app with all routers mounted."""
    # ADR-0511: Configure marketplace index (JSON source of truth)
    try:
        from .routes.marketplace import resolve_index_path, set_marketplace_index_path
        set_marketplace_index_path(resolve_index_path())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to set marketplace index path: {e}")

    # Session lifecycle management (recovery + cleanup)
    from contextlib import asynccontextmanager
    from .session_manager import bootstrap_session_manager, get_session_manager

    @asynccontextmanager
    async def app_lifespan(app: FastAPI):
        # Startup: recover sessions from disk
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info("🚀 CorvinOS Console starting up...")
            await bootstrap_session_manager()
            manager = get_session_manager()
            stats = manager.stats()
            logger.info(
                "✅ App ready. Sessions: total=%d active=%d expired=%d corrupted=%d",
                stats.total_sessions,
                stats.active_sessions,
                stats.expired_sessions,
                stats.corrupted_sessions,
            )
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.error("❌ CRITICAL: Failed to bootstrap session manager: %s", exc)
            logger.error("Cannot start console without session recovery.")
            raise  # CRITICAL FIX: Fail startup on bootstrap failure (HIGH #9)

        yield

        # Shutdown: cleanup task (optional — can be extended for graceful cleanup)
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info("🛑 CorvinOS Console shutting down...")
            manager = get_session_manager()
            await manager.cleanup_expired_sessions(max_age_s=86400)
            logger.info("✅ Session cleanup complete")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Session cleanup during shutdown failed: %s", exc)

    _app = FastAPI(
        title="CorvinOS Console",
        version="0.1.0",
        lifespan=app_lifespan,
    )
    _app.include_router(router)  # All API routes (includes /vibe-engineering/*)
    mount_static(_app, url_prefix="/console")  # SPA at /console/
    return _app

# Uvicorn entry point
app = create_app()
