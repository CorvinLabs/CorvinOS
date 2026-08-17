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
GATED_FLAGS: tuple[str, ...] = (
    "vibe_engineering_active",
    "console_web_surface_plugin",
    "dual_gate_pipeline_enabled",
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


@router.get("")
async def get_capabilities(session: Any = Depends(require_session)) -> dict:
    """Return the versioned capability manifest for the caller's tenant."""
    tenant_id = getattr(session, "tenant_id", None) or "_default"
    return {
        "contract_version": CONTRACT_VERSION,
        "capabilities": list(CORE_CAPABILITIES),
        "flags": _read_flags(tenant_id),
    }
