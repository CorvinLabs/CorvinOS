"""Feature Configuration Pipeline — config-dict API (ADR-0111).

Replaces the boolean ``is_feature_allowed()`` gate pattern with a
data-driven pattern where features receive a configuration dict from
the unsealed SOB.  A ``None`` result means "no config → feature does not
initialise", which is a stronger guarantee than a bool-check that can be
patched to ``return True``.

Usage::

    from operator.license.sob import SobClient
    from operator.license.capability import Capability

    sob = SobClient(corvin_home)
    sob.load()
    cap = Capability(sob)

    # Feature config — replaces: if is_feature_allowed("data_residency")
    zone_cfg = cap.get_feature_config("data_residency")
    if zone_cfg is None:
        return   # feature has no configuration → skip
    enforce_zone_routing(zones=zone_cfg["zones"], strict=zone_cfg["strict"])

    # Numeric limits — same semantics as the legacy get_limit()
    max_tenants = cap.get_limit("tenants_max")   # None = unlimited

    # Boolean feature gate (legacy compat, lower security than config-dict)
    if cap.is_feature_allowed("sso_enabled"):
        ...
"""
from __future__ import annotations

from typing import Any

from .limits import FREE_TIER, TIER_RESOURCE_LIMITS

try:
    from .validator import _audit as _cap_audit
except ImportError:
    def _cap_audit(event: str, **kw) -> None:  # type: ignore[misc]
        pass


class Capability:
    """Thin, session-scoped wrapper around a ``SobClient``.

    Constructed once per adapter session after ``SobClient.load()``.
    All methods are read-only; the underlying SobClient can be reloaded
    via ``sob.reload()`` followed by constructing a new ``Capability``,
    or simply call ``Capability.refresh()`` which does both.
    """

    def __init__(self, sob_client: Any) -> None:
        self._sob = sob_client

    # ── Feature config dict (primary, high-security API) ─────────────────────

    def get_feature_config(self, feature: str) -> dict[str, Any] | None:
        """Return the feature configuration dict, or ``None`` if not licensed.

        The config dict schema is feature-specific and versioned.  It is
        deliberately undocumented in open-source to prevent mock-bypass:
        a mock of ``seal_loader.unseal()`` that returns arbitrary dicts
        must still reverse-engineer each feature's expected keys.

        Example (data_residency)::
            {"zones": ["eu-west-1"], "strict": True}

        Example (audit_export)::
            {"formats": ["jsonl", "csv"]}
        """
        claims = self._sob.get_claims()
        if claims is None:
            return None
        features = claims.get("features")
        if not isinstance(features, dict):
            return None
        cfg = features.get(feature)
        if not isinstance(cfg, dict):
            return None
        return cfg

    # ── Numeric limits (retained from validator.py for quota checks) ──────────

    def get_limit(self, feature: str) -> Any:
        """Return the current limit for a feature.

        Resolution order (ADR-0094):
          1. Active SOB's ``limits`` dict (per-customer override)
          2. TIER_RESOURCE_LIMITS[tier] (tier-level default)
          3. FREE_TIER (absolute fallback)

        Returns ``None`` for "no constraint" (unlimited).
        """
        claims = self._sob.get_claims()
        if claims is None:
            return FREE_TIER.get(feature, 0)
        limits = claims.get("limits", {})
        if isinstance(limits, dict) and feature in limits:
            val = limits[feature]
            # ADR-0144 string-coercion guard (mirrors validator._resolve_limit):
            # a string value in the signed limits dict (e.g. "9999") would coerce
            # via int() in assert_limit and silently grant an arbitrary cap. Reject
            # strings — fall through to the tier default so the mis-serialised value
            # has no effect. None, bool, int, float, and list are all valid types.
            if isinstance(val, str):
                pass  # fall through to tier/free-tier default below
            elif (
                isinstance(val, (int, float))
                and not isinstance(val, bool)
                and val < 0
            ):
                # ADR-0144 negative-limit guard: int(-1) makes any `requested > limit`
                # check behave pathologically; clamp to 0 so the denial is explicit
                # and audit-visible rather than a silent over-grant or silent DoS.
                return 0
            else:
                return val
        tier = claims.get("tier", "free")
        tier_limits = TIER_RESOURCE_LIMITS.get(tier, {})
        if feature in tier_limits:
            return tier_limits[feature]
        return FREE_TIER.get(feature, 0)

    def assert_limit(self, feature: str, requested: Any = 1) -> None:
        """Raise ``LicenseLimitError`` when requested exceeds the limit.

        Semantics by limit type:
          bool   → raise if False
          list   → raise if requested not in list
          int    → raise if requested > limit
          None   → never raise (unlimited)
        """
        from .limits import LicenseLimitError
        limit = self.get_limit(feature)
        tier = self._sob.active_tier()

        if limit is None:
            return

        def _emit_and_raise(*args: Any, **kwargs: Any) -> None:
            _cap_audit(
                "license.limit_exceeded",
                tier=str(tier or ""),
                capability=str(feature or ""),
                limit=str(kwargs.get("limit", limit) or ""),
                current=str(kwargs.get("requested", requested) or ""),
            )
            raise LicenseLimitError(*args, **kwargs)

        # Reject negative values — they pass any `> limit` check silently.
        if isinstance(requested, (int, float)) and not isinstance(requested, bool):
            if requested < 0:
                _emit_and_raise(feature, requested=requested, limit=limit, tier=tier)

        if isinstance(limit, bool):
            if not limit:
                _emit_and_raise(feature, tier=tier)
            # bool True ≡ limit=1; a requested > 1 must still be blocked.
            if isinstance(requested, (int, float)) and not isinstance(requested, bool) and requested > 1:
                _emit_and_raise(feature, requested=requested, limit=1, tier=tier)
            return

        if isinstance(limit, list):
            if requested not in limit:
                _emit_and_raise(feature, requested=requested, limit=limit, tier=tier)
            return

        import math as _math
        try:
            _req_int = (
                _math.ceil(requested) if isinstance(requested, float)
                and not isinstance(requested, bool) else int(requested)
            )
            if _req_int > int(limit):
                _emit_and_raise(feature, requested=requested, limit=limit, tier=tier)
        except LicenseLimitError:
            raise
        except (TypeError, ValueError):
            _emit_and_raise(feature, tier=tier)

    # ── Boolean compat (lower security, retained for legacy call-sites) ───────

    def is_feature_allowed(self, feature: str) -> bool:
        """True when a boolean feature is enabled.

        Prefer ``get_feature_config()`` for new code — it cannot be bypassed
        by patching this method to return True.
        """
        # First check the high-security config-dict path
        if self.get_feature_config(feature) is not None:
            return True
        # Fall back to limit value for bool-typed limits
        val = self.get_limit(feature)
        if isinstance(val, bool):
            return val
        if val is None:
            return False   # not configured = denied; add to FREE_TIER or TIER_RESOURCE_LIMITS
        return bool(val)

    # ── Convenience ───────────────────────────────────────────────────────────

    def active_tier(self) -> str:
        return self._sob.active_tier()

    def is_loaded(self) -> bool:
        return self._sob.is_loaded()

    def refresh(self) -> bool:
        """Reload the SOB from disk (after a successful ``corvin-refresh``)."""
        return self._sob.reload()


# ── Module-level singleton ────────────────────────────────────────────────────
#
# Adapter code that currently uses ``from license.validator import get_limit``
# should migrate to using a ``Capability`` instance.  This singleton provides
# a migration path: create it once at boot, share it everywhere.
#
# Usage:
#   from operator.license.capability import get_capability
#   cap = get_capability()   # returns the module-level Capability singleton
#
# Call ``init_capability(sob_client)`` once at adapter boot.

_global_capability: Capability | None = None


def init_capability(sob_client: Any) -> Capability:
    """Initialise the module-level singleton.  Call once at adapter boot."""
    global _global_capability
    _global_capability = Capability(sob_client)
    return _global_capability


def get_capability() -> Capability | None:
    """Return the module-level singleton, or None if not yet initialised."""
    return _global_capability
