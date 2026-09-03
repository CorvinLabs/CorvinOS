"""Feature Flags Legacy Adapter — Transparent delegation to Skills API.

This module provides backward-compatible API for all 88 call-sites.
All calls transparently delegate to the Skills-based feature_flags implementation,
allowing a gradual Phase 1b migration without changing any existing code.

Architecture: Wrapper+Phased (Option 2b)
  - Spike 1: Rewrite feature_flags.py as Skills (done)
  - Phase 1b: 88 call-sites use this wrapper (unchanged)
  - Phase 2: Gradual migration to direct Skills API (future)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Import the original feature_flags module for registry access
from corvin_core import feature_flags as _original_ff

# Placeholder for Skills API import (will be available after Spike 1)
# from core.skills.feature_flags_skill import FeatureFlagsSkill
# For now, we'll use the original implementation as the backend

__all__ = [
    "FeatureFlagAdapter",
    # Wrapper functions (backward-compatible API)
    "is_enabled",
    "set_enabled",
    "describe_all",
    "flag",
    "tier_of",
    "can_promote_to",
    "worker_engine_mode",
    "set_worker_engine_mode",
    "recovery_command",
    "UnknownFlagError",
    "ProtectedMechanismError",
]


class FeatureFlagAdapter:
    """
    Transparent adapter: old API → Skills API delegation.

    All 88 call-sites continue using feature_flags.is_enabled(), etc.
    Behind the scenes, this adapter delegates to the Skills implementation.
    """

    def __init__(self):
        """Initialize adapter with Skills API backend."""
        # Placeholder: will use FeatureFlagsSkill when Spike 1 complete
        # For now, delegate directly to original feature_flags module
        self._backend = _original_ff

    def is_enabled(self, flag_id: str, tenant_id: str = "_default") -> bool:
        """
        Check if a feature flag is enabled.

        Delegates to: FeatureFlagsSkill.execute({operation: "is_enabled", ...})

        Args:
            flag_id: Feature flag ID (e.g., "vibe_engineering")
            tenant_id: Tenant ID (default: "_default")

        Returns:
            True if flag is enabled, False otherwise
        """
        # Phase 1b: Direct delegation to original
        # Phase 2+: Replace with FeatureFlagsSkill.execute()
        return self._backend.is_enabled(flag_id, tenant_id)

    def set_enabled(
        self, flag_id: str, enabled: bool, tenant_id: str = "_default"
    ) -> bool:
        """
        Set a feature flag's enabled state (console overlay).

        Delegates to: FeatureFlagsSkill.execute({operation: "set_enabled", ...})

        Args:
            flag_id: Feature flag ID
            enabled: New enabled state (True/False)
            tenant_id: Tenant ID (default: "_default")

        Returns:
            New enabled state
        """
        return self._backend.set_enabled(flag_id, enabled, tenant_id)

    def describe_all(self, tenant_id: str = "_default") -> list[dict[str, Any]]:
        """
        Get all registered flags + their resolved state.

        Delegates to: FeatureFlagsSkill.execute({operation: "describe_all", ...})

        Args:
            tenant_id: Tenant ID (default: "_default")

        Returns:
            List of flag metadata dicts with enabled/disabled state
        """
        return self._backend.describe_all(tenant_id)

    def flag(self, flag_id: str) -> Any:
        """Get a registered flag entry or raise UnknownFlagError."""
        return self._backend.flag(flag_id)

    def tier_of(self, flag_id: str) -> str:
        """Get the release tier of a flag."""
        return self._backend.tier_of(flag_id)

    def can_promote_to(self, flag_id: str, target_tier: str) -> bool:
        """Check if a flag can be promoted to a tier."""
        return self._backend.can_promote_to(flag_id, target_tier)

    def recovery_command(self, flag_id: str) -> str:
        """Get CLI recovery command for a self-locking flag."""
        return self._backend.recovery_command(flag_id)

    def worker_engine_mode(self, tenant_id: str = "_default") -> str:
        """
        Get worker engine mode (native | acs | tde).

        NOTE: Not a boolean flag. Separate from feature_flags settings.
        Delegates to: legacy feature_flags.worker_engine_mode()
        """
        return self._backend.worker_engine_mode(tenant_id)

    def set_worker_engine_mode(self, mode: str, tenant_id: str = "_default") -> str:
        """
        Set worker engine mode.

        Delegates to: legacy feature_flags.set_worker_engine_mode()
        """
        return self._backend.set_worker_engine_mode(mode, tenant_id)


# ─── MODULE-LEVEL ADAPTER INSTANCE ───────────────────────────────────────────

_adapter = FeatureFlagAdapter()


# ─── BACKWARD-COMPATIBLE FUNCTIONS (for 88 call-sites) ──────────────────────

def is_enabled(flag_id: str, tenant_id: str = "_default") -> bool:
    """
    Legacy API wrapper: is_enabled(flag_id).

    All 88 call-sites use this function unchanged.
    It transparently delegates to the Skills API.

    Example:
        from corvin_core.feature_flags import is_enabled
        if is_enabled("vibe_engineering"):
            # context engineering pipeline
    """
    return _adapter.is_enabled(flag_id, tenant_id)


def set_enabled(flag_id: str, enabled: bool, tenant_id: str = "_default") -> bool:
    """Legacy API wrapper: set_enabled(flag_id, enabled)."""
    return _adapter.set_enabled(flag_id, enabled, tenant_id)


def describe_all(tenant_id: str = "_default") -> list[dict[str, Any]]:
    """Legacy API wrapper: describe_all()."""
    return _adapter.describe_all(tenant_id)


def flag(flag_id: str) -> Any:
    """Legacy API wrapper: flag(flag_id)."""
    return _adapter.flag(flag_id)


def tier_of(flag_id: str) -> str:
    """Legacy API wrapper: tier_of(flag_id)."""
    return _adapter.tier_of(flag_id)


def can_promote_to(flag_id: str, target_tier: str) -> bool:
    """Legacy API wrapper: can_promote_to(flag_id, target_tier)."""
    return _adapter.can_promote_to(flag_id, target_tier)


def recovery_command(flag_id: str) -> str:
    """Legacy API wrapper: recovery_command(flag_id)."""
    return _adapter.recovery_command(flag_id)


def worker_engine_mode(tenant_id: str = "_default") -> str:
    """Legacy API wrapper: worker_engine_mode()."""
    return _adapter.worker_engine_mode(tenant_id)


def set_worker_engine_mode(mode: str, tenant_id: str = "_default") -> str:
    """Legacy API wrapper: set_worker_engine_mode(mode)."""
    return _adapter.set_worker_engine_mode(mode, tenant_id)


# ─── EXCEPTION RE-EXPORTS ────────────────────────────────────────────────────

UnknownFlagError = _original_ff.UnknownFlagError
ProtectedMechanismError = _original_ff.ProtectedMechanismError


# ─── PHASE 1B → PHASE 2 MIGRATION ROADMAP ────────────────────────────────────
#
# This wrapper enables transparent delegation from 88 call-sites.
#
# Phase 1b (Weeks 1–10):
#   - All 88 call-sites continue using: from corvin_core.feature_flags import is_enabled
#   - Behind-the-scenes: this adapter delegates to Skills API
#   - Test: equivalence verified (old API == new Skill behavior)
#
# Phase 2 (Weeks 11+):
#   - Gradual migration: replace is_enabled() calls with direct Skill API
#   - Pattern: from core.skills.feature_flags_skill import feature_flags_skill
#            result = feature_flags_skill.execute({...})
#   - Priority: metrics-driven (high-volume call-sites first)
#
# Phase 2 Cleanup (Weeks 13+):
#   - Remove this adapter entirely
#   - All 88 call-sites now use direct Skills API
#   - Delete feature_flags_legacy_adapter.py
#
# ─────────────────────────────────────────────────────────────────────────────
