"""``GET /v1/console/capabilities`` — the versioned capability manifest (ADR-0357, P3).

The shell renders its navigation and routes from THIS manifest instead of trusting
its own hardcoded panel list. A panel in the frontend registry declares a
``requiredCapability`` and/or ``requiredFlag`` (ADR-0353 P1 already added those
fields); the shell mounts a panel only when the manifest reports the capability
present and the flag on. The ``contract_version`` lets the shell refuse to render
against a manifest whose shape it does not understand — the forward-compat hinge
for P4 (panel SDK) and P7 (loader-supplied panels).

Why a server manifest at all: today the panel list lives in the SPA bundle, so a
headless or stripped install, an operator who disabled a subsystem, or (P7) a
loader-supplied external panel would all be invisible to the shell's own idea of
"what exists". The backend is the only place that knows which capabilities are
actually wired and which flags are on for THIS tenant. This route is that SSOT.

Scope (P3): the capability list is the set the backend provides; the flags are
read live per tenant. Deriving the capability list dynamically from the mounted
routers — so it can never drift from what is actually served — is a later
refinement (P7, when panels register as plugins). The version discipline is what
makes that later change non-breaking.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import require_session

router = APIRouter(prefix="/capabilities", tags=["console-capabilities"])

#: Bump when the manifest SHAPE changes in a way the shell must understand
#: (a renamed/removed field, a changed gating semantic). Adding a capability id or
#: a flag to the lists below is NOT a shape change and does NOT bump this.
CONTRACT_VERSION = "1"

#: The capabilities the backend provides, keyed by the id the frontend registry
#: gates against (ConsolePanel.requiredCapability / the panel route id). SSOT for
#: "what panels may the shell mount". Keep in sync with the panel registry until
#: P7 derives this from the plugin loader.
CORE_CAPABILITIES: tuple[str, ...] = (
    "dashboard", "sessions", "audit", "tasks", "personas", "engines",
    "bridges", "voice", "forge", "skills", "packages", "cowork", "ldd",
    "compliance", "files", "memory", "compute", "browser", "space",
    "talent", "settings", "vibe-engineering",
)

#: Feature flags a panel may gate on (ConsolePanel.requiredFlag). Read live per
#: tenant. A flag absent from the flags module resolves to False (ship-dark safe).
#: ALL FLAGS FROM feature_flags.REGISTRY MUST BE HERE to be discoverable in the
#: Settings UI. Missing here = manifest never carries the key = flag stays hidden forever.
GATED_FLAGS: tuple[str, ...] = (
    # Core UI flags
    "vibe_engineering",
    "vibe_engineering_active",
    "console_web_surface_plugin",
    "console_auto_reload",
    "console_marketplace_panel",
    "frontend_forge",
    "package_marketplace_ui",

    # Validation & Security
    "validator_factory_enabled",
    "file_permissions_enabled",
    "dual_gate_pipeline_enabled",
    "dual_gate_pii_detection_enabled",
    "dual_gate_queue_integrity_enabled",
    "queue_corruption_detection_enabled",

    # Execution & Context
    "execution_context_badge",
    "vibe_engineering_active",
    "auto_load_github_repo",
    "cel_cache_stable",
    "cel_brief_includes_content",
    "cel_load_bearing_anchor",

    # Delegation & Models
    "ccc_command_routing",
    "acs_context_sync",
    "tde_shadow_measurement",
    "tde_measurement_collection",
    "bridge_task_supervision",
    "bridge_task_progress_updates",
    "bridge_big_data_delegation",
    "bridge_worker_engine_parity",
    "bridge_tde_execution",
    "delegation_badge",
    "live_model_discovery",
    "model_catalog_auto_refresh",

    # Plugins & Extensions
    "plugin_health_monitoring",
    "plugin_runtime_lifecycle",
    "plugin_trust_enforcement",
    "plugin_self_healing",
    "plugin_console_surface",
    "plugin_extension_points",
    "admin_control_plane",
    "bridge_supervisor_plugins",

    # Plugin Builder
    "plugin_builder_enabled",
    "plugin_builder_idea_first_interview",
    "plugin_builder_checkpoint_review",
    "plugin_builder_generate_e2e_tests",
    "plugin_builder_ideas_mode",

    # A2A & Network
    "a2a_relay_fallback",
    "a2a_lan_bind",

    # Advanced Features
    "headless_api_mode",
    "browser_automation",

    # Learning Infrastructure
    "outcome_feedback_loop",
    "cross_device_sync",
    "memory_confidence_gate_enabled",
    "per_stage_token_budgeting",
    "adaptive_context_routing",
    "learning_gap_3_attribution",
    "learning_gap_6_cost_learning",
    "learning_gap_7_operator_feedback",
    "skill_forge_enabled",
)


def _read_flags(tenant_id: str) -> dict[str, bool]:
    """Read the gated flags for a tenant. Every failure mode (module absent, flag
    unregistered, overlay unreadable) resolves the flag to False — a panel gated on
    an unreadable flag stays hidden, which is the safe direction."""
    try:
        from corvin_core.feature_flags import is_enabled  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return {flag: False for flag in GATED_FLAGS}
    out: dict[str, bool] = {}
    for flag in GATED_FLAGS:
        try:
            out[flag] = bool(is_enabled(flag, tenant_id))
        except Exception:  # noqa: BLE001 — an unreadable flag is an off flag
            out[flag] = False
    return out


def _get_plugin_panels() -> list[dict]:
    """Get all auto-registered plugin panels (Phase 3 Integration).

    Returns panels registered by installed plugins via PluginPanelRegistry.
    Degrades gracefully if registry is unavailable (panel registry not yet created).
    """
    try:
        from core.plugins.plugin_panel_registry import get_panel_registry
        registry = get_panel_registry()
        panels = registry.get_all_enabled_panels()
        return [
            {
                "id": p["panel_id"],
                "plugin_id": p["plugin_id"],
                "label": p["label"],
                "route": p["route"],
                "icon": p["icon"],
                "group": p["group"],
            }
            for p in panels
        ]
    except Exception:  # noqa: BLE001
        # Registry unavailable: no plugin panels available, but Console still works
        return []


@router.get("")
async def get_capabilities(session: Any = Depends(require_session)) -> dict:
    """Return the versioned capability manifest for the caller's tenant."""
    tenant_id = getattr(session, "tenant_id", None) or "_default"
    return {
        "contract_version": CONTRACT_VERSION,
        "capabilities": list(CORE_CAPABILITIES),
        "flags": _read_flags(tenant_id),
        "plugin_panels": _get_plugin_panels(),  # Phase 3: auto-registered panels
    }


def _loaded_web_surfaces() -> list[dict]:
    """List the web_surface plugins the plugin loader actually loaded (ADR-0365 P7).

    Closes the P2.5→P7 loop: the Console (and any future UI) is DECLARED as a
    web_surface plugin (ADR-0356), the loader loads it when its ship-dark flag is on,
    and this reads them back from the live registry so the shell can see which
    surfaces exist — not what the SPA bundle happened to hardcode. Every failure
    (registry absent, plugin missing an attr) degrades to an empty list: on a default
    install no web_surface is declared, so this is empty, and the shell falls back to
    its built-in panel list exactly as before (ship-dark)."""
    try:
        from corvin_plugins.registry import plugins_by_boot_layer  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return []
    surfaces: list[dict] = []
    seen: set[str] = set()
    for boot_layer in ("bundled", "installed"):
        try:
            plugins = plugins_by_boot_layer(boot_layer)
        except Exception:  # noqa: BLE001
            continue
        for p in plugins:
            if getattr(p, "plugin_type", None) != "web_surface":
                continue
            pid = getattr(p, "plugin_id", None)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            has_spa = False
            try:
                fn = getattr(p, "spa_dist_dir", None)
                has_spa = callable(fn) and fn() is not None
            except Exception:  # noqa: BLE001 — a surface that can't answer is not mounted
                has_spa = False
            surfaces.append({
                "id": pid,
                "mount_path": getattr(p, "mount_path", None),
                "boot_layer": boot_layer,
                "has_spa": has_spa,
            })
    return surfaces


@router.get("/surfaces")
async def get_surfaces(session: Any = Depends(require_session)) -> dict:
    """Return the web_surface plugins the loader has loaded (ADR-0365 P7)."""
    return {"surfaces": _loaded_web_surfaces()}
