"""ADR-0156 M2 — Custom Layer License Gate.

Enforces the per-tier limit on simultaneously active Tier-B/C custom layers.
Tier-A (prompt / skill) layers are always free and are never counted here.

License limit key: ``active_custom_layers_bc``

| Tier    | Limit |
|---------|-------|
| free    | 1     |
| member  | None (unlimited) |
| universal (alias) | None (unlimited) |
| personal / pro / business / enterprise | None (unlimited) |

Gate philosophy (ADR-0154 OTA)
================================
The gate is **structural**, not conditional.  Tier-B/C layer activation in
``custom_layer_registry.install_layer()`` and any future ``enable_layer()``
caller MUST call :func:`check_layer_install` before proceeding — the gate is
not a flag check that can be patched by flipping a boolean.

Fail-closed contract
=====================
Any error reading the license (import failure, tamper canary mismatch, missing
module) is treated as **free tier** — the gate degrades to the most restrictive
setting, never the most permissive.

Constraints
============
* ``import anthropic`` is forbidden — CI AST lint enforces.
* ``import anthropic`` must NOT appear anywhere in this file.
* Never delete layers; only disable them (boot enforcement).
* Do NOT auto-re-enable layers after a license upgrade.

Audit events
=============
``custom_layer.boot_limit_exceeded`` — emitted per disabled layer at boot.
Allowed detail keys: ``layer_name``, ``tier``, ``reason``.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("corvin.custom_layer_gate")

# Feature key used in limits.py / validator.py
_FEATURE_KEY = "active_custom_layers_bc"

# Tiers that count as Tier-B/C gated (i.e. any tier where the limit is active).
# The gate applies to ALL tiers — it just happens that paid tiers have limit=None
# (unlimited) so the gate never blocks them in practice.
_BC_TIERS = frozenset({"B", "C"})


# ── Exception ─────────────────────────────────────────────────────────────────

class LayerLimitExceeded(Exception):
    """Raised by :func:`check_layer_install` when the active Tier-B/C layer
    count would exceed the limit for the current license tier.

    Callers must surface this as a hard refusal — do NOT catch and ignore.
    """

    def __init__(self, current: int, limit: int, license_tier: str):
        self.current = current
        self.limit = limit
        self.license_tier = license_tier
        super().__init__(
            f"Custom layer limit exceeded: tier '{license_tier}' allows "
            f"{limit} active Tier-B/C layer(s), but {current} are already active. "
            "Deactivate an existing Tier-B/C layer first, or upgrade at "
            "corvin-labs.com/pricing."
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_limit() -> "int | None":
    """Return the active ``active_custom_layers_bc`` limit.

    Fail-closed: any error → free-tier default (1).
    Returns ``None`` for unlimited.
    """
    try:
        # Path insertion so this module works both as a package member and
        # when loaded standalone (e.g. from tests via sys.path hacks).
        _lic_path = Path(__file__).resolve().parents[2] / "license"
        if str(_lic_path) not in sys.path:
            sys.path.insert(0, str(_lic_path))
        # ADR-0215 F5: dead dotted primary removed — the sys.path insert
        # above already makes the bare form below resolve directly.
        from validator import get_limit as _gl  # type: ignore[import]
        val = _gl(_FEATURE_KEY)
        # None = unlimited; int = hard cap; 0 = denied (also valid).
        if val is None:
            return None
        return int(val)
    except Exception as exc:
        log.warning(
            "custom_layer_gate: could not read license limit (%s) — "
            "defaulting to free-tier limit of 1 (fail-closed)",
            exc,
        )
        return 1  # free-tier fallback


def _get_active_tier() -> str:
    """Return the current license tier string ('free' on any error)."""
    try:
        _lic_path = Path(__file__).resolve().parents[2] / "license"
        if str(_lic_path) not in sys.path:
            sys.path.insert(0, str(_lic_path))
        # ADR-0215 F5: dead dotted primary removed (see _get_limit above).
        from validator import active_tier as _at  # type: ignore[import]
        return _at()
    except Exception:
        return "free"


def _count_active_bc(layers: "dict[str, Any]") -> int:
    """Count active Tier-B or Tier-C layers in a registry snapshot."""
    return sum(
        1 for rec in layers.values()
        if rec.active and rec.tier in _BC_TIERS
    )


def _audit_boot_disabled(layer_name: str, tier: str, license_tier: str,
                         tenant_id: str, limit: int) -> None:
    """Emit ``custom_layer.boot_limit_exceeded`` for one disabled layer."""
    try:
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        try:
            from . import audit as _audit_mod  # type: ignore[import]
        except ImportError:
            import audit as _audit_mod  # type: ignore[import]
        _audit_mod.audit_event(
            "custom_layer.boot_limit_exceeded",
            tenant_id=tenant_id,
            details={
                "layer_name": layer_name,
                "tier": tier,
                "reason": (
                    f"license_tier={license_tier} limit={limit} "
                    "excess_layer_disabled_at_boot"
                ),
            },
        )
    except Exception:  # pragma: no cover — best-effort
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def check_layer_install(tier: str, existing_active_bc_count: int) -> None:
    """Gate: raise :exc:`LayerLimitExceeded` if installing a Tier-B/C layer
    would exceed the license limit for the current tier.

    This is called from :func:`~custom_layer_registry.install_layer` before
    the layer files are copied.  For Tier-A layers the call is a no-op.

    Parameters
    ----------
    tier:
        The tier of the layer being installed (``"A"``, ``"B"``, or ``"C"``).
    existing_active_bc_count:
        Number of Tier-B/C layers that are currently **active** in the
        registry, not counting the layer being installed.

    Raises
    ------
    LayerLimitExceeded
        When ``tier`` is B or C and
        ``existing_active_bc_count >= limit`` (i.e. adding one more would
        exceed the cap).

    Notes
    -----
    The gate only fires for Tier-B/C.  Tier-A (prompt / skill injection) is
    always free.  A newly installed layer starts in the **disabled** state, so
    this gate fires based on how many are already *active*, not installed.

    The gate is intentionally conservative: it rejects even the install
    step so the user gets a clear error before any files are copied.
    """
    if tier not in _BC_TIERS:
        # Tier-A: always allowed.
        return

    limit = _get_limit()

    if limit is None:
        # Unlimited — gate open.
        return

    if existing_active_bc_count >= limit:
        license_tier = _get_active_tier()
        raise LayerLimitExceeded(
            current=existing_active_bc_count,
            limit=limit,
            license_tier=license_tier,
        )


def check_layer_boot(
    tenant_id: str | None = None,
    *,
    channel: str = "",
) -> list[str]:
    """Adapter-boot enforcement: disable excess Tier-B/C layers if the current
    license tier permits fewer than are currently active.

    Called once at adapter boot (soft enforcement).  Excess layers are
    **disabled** (not deleted) and a ``custom_layer.boot_limit_exceeded``
    WARNING audit event is emitted per disabled layer.  The most-recently-
    installed layers are kept active; the oldest excess layers are disabled
    (sorted by ``installed_at`` ascending so the most-recently-installed
    layers survive).

    This situation arises when a license downgrades (e.g. subscription lapses)
    between restarts.  Layers are NEVER auto-re-enabled on upgrade — the user
    must explicitly re-enable them.

    Parameters
    ----------
    tenant_id:
        Tenant to check.  Defaults to ``_default`` when ``None``.
    channel:
        Bridge channel name for audit metadata.

    Returns
    -------
    list[str]
        Names of layers that were disabled.  Empty list when no action was
        needed.
    """
    limit = _get_limit()
    if limit is None:
        # Unlimited — nothing to do.
        return []

    # Import registry helpers here to avoid circular imports at module load.
    try:
        try:
            from . import custom_layer_registry as _reg  # type: ignore[import]
        except ImportError:
            _reg_path = Path(__file__).resolve().parent
            if str(_reg_path) not in sys.path:
                sys.path.insert(0, str(_reg_path))
            import custom_layer_registry as _reg  # type: ignore[import]
    except Exception as exc:
        log.error(
            "custom_layer_gate.check_layer_boot: could not import registry (%s) — "
            "skipping boot enforcement",
            exc,
        )
        return []

    layers = _reg.load_registry(tenant_id)
    active_bc = [
        rec for rec in layers.values()
        if rec.active and rec.tier in _BC_TIERS
    ]

    if len(active_bc) <= limit:
        # Within the allowed cap — nothing to do.
        return []

    # Sort oldest-first by installed_at so the newest layers are kept active.
    active_bc.sort(key=lambda r: r.installed_at)

    # Disable the excess (everything beyond `limit` slots).
    excess = active_bc[: len(active_bc) - limit]
    disabled: list[str] = []
    license_tier = _get_active_tier()

    for rec in excess:
        try:
            _reg.disable_layer(rec.name, tenant_id, channel=channel)
            _audit_boot_disabled(
                layer_name=rec.name,
                tier=rec.tier,
                license_tier=license_tier,
                tenant_id=tenant_id or "_default",
                limit=limit,
            )
            log.warning(
                "custom_layer_gate: disabled Tier-%s layer '%s' at boot — "
                "license tier '%s' allows %d active Tier-B/C layer(s)",
                rec.tier, rec.name, license_tier, limit,
            )
            disabled.append(rec.name)
        except Exception as exc:
            log.error(
                "custom_layer_gate: failed to disable layer '%s' at boot: %s",
                rec.name, exc,
            )

    return disabled
