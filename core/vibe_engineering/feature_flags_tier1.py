"""Tier-1 Feature Flags: Activate autonomy features for production."""

import os
from typing import Dict


class FeatureFlagResolver:
    """Tier-1 feature flags for Week 4 production rollout."""

    TIER_1_FLAGS = {
        "task_orchestrator_multiphase": True,  # TaskOrchestrator + Registry (ENABLED)
        "auto_session_renewal": True,           # SessionRenewerEngine (ENABLED)
        "notification_system_v1": False,        # Discord notifications (OFF by default, operator opt-in)
    }

    TIER_2_FLAGS = {
        "adaptive_strategy_ranking": False,     # ADR-0370 integration (deferred to Week 5)
        "event_driven_notifications": False,    # Event subscriptions vs polling (deferred)
    }

    TIER_3_FLAGS = {
        "vibe_engineering_encryption": False,   # Checkpoint encryption (Phase 2)
    }

    @staticmethod
    def is_enabled(flag_id: str, default: bool = False) -> bool:
        """Check if feature flag is enabled."""
        # Check env var override first (for canary)
        env_var = f"CORVIN_FEATURE_{flag_id.upper()}"
        env_val = os.environ.get(env_var)
        if env_val is not None:
            return env_val.lower() in ("1", "true", "yes")

        # Check all tiers
        flags = {
            **FeatureFlagResolver.TIER_1_FLAGS,
            **FeatureFlagResolver.TIER_2_FLAGS,
            **FeatureFlagResolver.TIER_3_FLAGS,
        }
        return flags.get(flag_id, default)

    @staticmethod
    def get_all_flags() -> Dict[str, bool]:
        """Get all flags (for operator settings panel)."""
        return {
            **FeatureFlagResolver.TIER_1_FLAGS,
            **FeatureFlagResolver.TIER_2_FLAGS,
            **FeatureFlagResolver.TIER_3_FLAGS,
        }


# Singleton
_resolver = FeatureFlagResolver()


def is_feature_enabled(flag_id: str) -> bool:
    """Convenience function."""
    return _resolver.is_enabled(flag_id)
