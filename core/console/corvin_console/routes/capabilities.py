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
    """Read the gated flags for a tenant via os.capabilities Skill.

    Phase 1 k=2-5 refactoring: Uses Skill instead of feature_flags module.
    Every failure mode (Skill unavailable, flag unregistered) resolves the flag to
    False — a panel gated on an unreadable flag stays hidden, which is the safe direction.
    """
    try:
        from core.skills.skill_registry_phase1 import get_registry
    except Exception:  # noqa: BLE001
        return {flag: False for flag in GATED_FLAGS}

    try:
        registry = get_registry()
        result = registry.execute("os.capabilities", {
            "tenant_id": tenant_id,
            "gated_flags": list(GATED_FLAGS),
        })

        if result.status == "success":
            return result.output.get("flags", {flag: False for flag in GATED_FLAGS})
        return {flag: False for flag in GATED_FLAGS}
    except Exception:  # noqa: BLE001 — Skill execution failure → all flags off
        return {flag: False for flag in GATED_FLAGS}


def _get_plugin_panels() -> list[dict]:
    """Get all auto-registered plugin panels (Phase 3 Integration).

    Returns panels registered by installed plugins via PluginPanelRegistry.
    Schema matches frontend PanelDescriptor (ADR-0561 Phase 3).
    Degrades gracefully if registry is unavailable (panel registry not yet created).
    """
    try:
        from core.plugins.plugin_panel_registry import get_panel_registry
        registry = get_panel_registry()
        panels = registry.get_all_enabled_panels()
        return [
            {
                "id": f"plugin-{p['plugin_id']}",
                "title": p.get("label", p["plugin_id"]),
                "route": p["route"],
                "icon": p.get("icon", "Package"),
                "kind": "plugin",
                "source": "installed",
                "nav_group": p.get("group", "plugins"),
                "requiredFlag": None,
                "requiredCapability": None,
                "element": {
                    "kind": "plugin-inspector",
                    "plugin_id": p["plugin_id"],
                },
                "version": p.get("version", "1.0.0"),
                "audit_events": ["console_panel_opened", "plugin_executed"],
                "tenant_scoped": True,
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADR-0561: Console UI System Redesign — Unified Panel Manifest
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import hashlib
import json
from datetime import datetime


def _get_builtin_panels() -> list[dict]:
    """All builtin (hardcoded) Console panels (ADR-0561 Phase 1)."""
    return [
        # Primary group
        {
            "id": "chat",
            "title": "Chat",
            "route": "chat",
            "icon": "MessagesSquare",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "primary",
            "requiredFlag": None,
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "ChatPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        {
            "id": "dashboard",
            "title": "Dashboard",
            "route": "dashboard",
            "icon": "LayoutDashboard",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "primary",
            "requiredFlag": None,
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "DashboardPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        # Vibe Engineering group (ADR-0561 Phase 4-5)
        {
            "id": "vibe-engineering",
            "title": "Dashboard",
            "route": "vibe-engineering",
            "icon": "Layers",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "vibe",
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "VibeDashboard"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        {
            "id": "brain-monitor",
            "title": "Brain Monitor",
            "route": "brain-monitor",
            "icon": "Cpu",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "vibe",
            "requiredFlag": "vibe_engineering",
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "BrainMonitorPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        # More builtin panels...
        {
            "id": "skills",
            "title": "Skills",
            "route": "skills",
            "icon": "BookOpen",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "build",
            "requiredFlag": None,
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "SkillsPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        {
            "id": "plugins",
            "title": "Plugins & Extensions",
            "route": "plugin-center",
            "icon": "Blocks",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "build",
            "requiredFlag": None,
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "PluginCenterPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
        {
            "id": "settings",
            "title": "Settings",
            "route": "settings",
            "icon": "Settings",
            "kind": "feature",
            "source": "builtin",
            "nav_group": "system",
            "requiredFlag": None,
            "requiredCapability": None,
            "element": {"kind": "react-component", "component": "SettingsPage"},
            "version": "1.0.0",
            "audit_events": ["console_panel_opened"],
            "tenant_scoped": True,
        },
    ]


def _get_skill_panels(gated_flags: dict[str, bool]) -> list[dict]:
    """Auto-registered panels for all installed Skills (ADR-0561 Phase 3)."""
    try:
        from core.skills.skill_registry_phase1 import get_registry
        registry = get_registry()
        skills = registry.list_all()

        panels = []
        for skill in skills:
            skill_id = getattr(skill, "id", None)
            if not skill_id:
                continue

            # Only OS-skills get auto-panels for now
            if not skill_id.startswith("os."):
                continue

            panels.append({
                "id": f"skill-{skill_id.replace('.', '-')}",
                "title": getattr(skill, "title", skill_id),
                "route": f"skills/{skill_id.replace('.', '-')}",
                "icon": "Zap",
                "kind": "skill",
                "source": "builtin",
                "nav_group": "build",
                "requiredFlag": None,
                "requiredCapability": None,
                "element": {
                    "kind": "skill-inspector",
                    "skill_id": skill_id,
                },
                "version": getattr(skill, "version", "1.0.0"),
                "audit_events": ["console_panel_opened", "skill_executed"],
                "tenant_scoped": True,
            })
        return panels
    except Exception:  # noqa: BLE001
        return []


def _get_nav_groups(panels: list[dict], flags: dict[str, bool]) -> list[dict]:
    """Generate nav groups from gated panels (ADR-0561)."""
    return [
        {
            "id": "primary",
            "label": None,
            "collapsible": False,
            "defaultOpen": True,
            "items": [
                {"panel_id": "chat"},
                {"panel_id": "dashboard"},
            ],
        },
        {
            "id": "vibe",
            "label": "Vibe Engineering",
            "collapsible": True,
            "defaultOpen": True,
            "items": [
                {"panel_id": "vibe-engineering"},
                {"panel_id": "brain-monitor"},
            ],
        },
        {
            "id": "build",
            "label": "Build",
            "collapsible": True,
            "defaultOpen": True,
            "items": [
                {"panel_id": "skills"},
                {"panel_id": "plugins"},
            ] + [
                {"panel_id": p["id"]} for p in panels
                if p["kind"] == "skill" and p["nav_group"] == "build"
            ],
        },
        {
            "id": "system",
            "label": "System",
            "collapsible": True,
            "defaultOpen": False,
            "items": [
                {"panel_id": "settings"},
            ],
        },
    ]


def _compute_manifest_hash(manifest: dict) -> str:
    """Compute a stable hash of the manifest for caching/invalidation (ADR-0561)."""
    # Hash panels + nav_groups (ignore timestamps and hash itself)
    data = {
        "panels": [
            {k: v for k, v in p.items() if k != "lom"}
            for p in manifest["panels"]
        ],
        "nav_groups": manifest["nav_groups"],
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


@router.get("/manifest")
async def get_console_manifest(session: Any = Depends(require_session)) -> dict:
    """
    GET /api/console/manifest — Unified panel manifest (ADR-0561, Phase 1).

    Backend-driven Console: all panels (builtin, plugin, skill, ai-generated),
    nav structure, gating, versioned. Frontend renders from this manifest.

    Constraints (ADR-0561 Synthesis):
    - 200ms timeout; fallback to cached/builtin on slow endpoint
    - Hash-based invalidation on registry changes
    - Dual-layer gating (manifest + route)
    - Bundle review for custom plugins
    - Manifest v2.0 with forward compat
    """
    tenant_id = getattr(session, "tenant_id", None) or "_default"

    # Read flags (gating logic)
    flags = _read_flags(tenant_id)

    # Collect all panels (builtin + plugin + skill + ai-generated)
    all_panels = _get_builtin_panels()
    all_panels.extend(_get_plugin_panels())
    all_panels.extend(_get_skill_panels(flags))

    # Gate panels by capability + flag
    gated_panels = [
        p for p in all_panels
        if (
            (p["requiredCapability"] is None or p["requiredCapability"] in CORE_CAPABILITIES)
            and (p["requiredFlag"] is None or flags.get(p["requiredFlag"], False))
        )
    ]

    # Build nav structure from gated panels
    nav_groups = _get_nav_groups(gated_panels, flags)

    # Construct manifest
    manifest = {
        "version": "2.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "contract_version": CONTRACT_VERSION,
        "capabilities": list(CORE_CAPABILITIES),
        "flags": flags,
        "panels": gated_panels,
        "nav_groups": nav_groups,
    }

    # Compute hash for caching + invalidation
    manifest_hash = _compute_manifest_hash(manifest)
    manifest["hash"] = manifest_hash

    # Audit
    try:
        from core.learning.event_emitter import audit_log
        audit_log("console_manifest_generated", {
            "num_panels": len(gated_panels),
            "hash": manifest_hash,
            "tenant_id": tenant_id,
        })
    except Exception:  # noqa: BLE001
        pass

    return manifest
