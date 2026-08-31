"""
Feature Flag Resolver for Vibe Engineering

Enables safe gradual rollout with kill switches and dependency validation.

**NOT the canonical flag registry, and NOT read by anything in production**
(audited 2026-08-28). `get_resolver()` is called only by the four convenience
functions below, and nothing calls those — a `grep` for them outside this file
and its tests returns nothing. The registry the running system actually reads
is `core/console/corvin_core/feature_flags.py`, which resolves
`features.json` → `spec.features.<id>` → the registry default, is what the
Console Settings panel writes, and is what `_validate_registry` guards against
flagging a compliance mechanism.

Add a NEW feature flag THERE, never here. An entry added to `DEFAULTS` below
gates nothing, and a `True` one is a ship-dark violation that looks like a
shipped decision. This module is kept because its dependency-validation logic
is still referenced by the Vibe design docs; treat it as a design artifact
until it is either wired to the canonical registry or removed.
"""

import os
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class FeatureFlagResolver:
    """
    Resolves feature flags for Vibe Engineering subsystems.

    Guarantees:
    - Safe defaults (all flags default to OFF)
    - Dependency validation (can't enable child without parent)
    - Graceful degradation (flag failure doesn't crash task)
    """

    # Default feature configuration — every entry is False.
    #
    # CLAUDE.md § Feature Flags is load-bearing: a flag is off on a fresh
    # install and off after an upgrade. Three of these were True with the note
    # "direct 100% deployment (single-user environment)", which is not a
    # deployment mode this file can grant — see the module docstring: nothing
    # in production reads this resolver, so the True values enabled nothing and
    # only modelled a policy the canonical registry forbids.
    DEFAULTS = {
        "vibe_engineering_v0_2": False,
        "vibe_engineering_encryption": False,       # Checkpoint encryption (Phase 2)
        "vibe_engineering_ml_classifiers": False,   # ML-based tiers (Phase 2)
        "vibe_engineering_monitoring_dashboard": False,  # Dashboard (Phase 3.1)
    }

    # Dependency graph: feature -> required parents
    DEPENDENCIES = {
        "vibe_engineering_encryption": ["vibe_engineering_v0_2"],
        "vibe_engineering_ml_classifiers": ["vibe_engineering_v0_2"],
        "vibe_engineering_monitoring_dashboard": ["vibe_engineering_v0_2"],
    }

    def __init__(self, spec_features: Optional[Dict[str, bool]] = None):
        """
        Initialize resolver.

        Args:
            spec_features: Dict of feature flags (e.g., from spec.features)
                          If None, uses environment variables + defaults
        """
        self.features = self.DEFAULTS.copy()

        if spec_features:
            self.features.update(spec_features)
        else:
            # Fall back to environment variables (for testing)
            for flag, default in self.DEFAULTS.items():
                env_var = f"CORVIN_{flag.upper()}"
                value = os.environ.get(env_var)
                if value:
                    self.features[flag] = value.lower() in ("true", "1", "yes")

        # Validate dependencies
        self._validate_dependencies()

    def _validate_dependencies(self):
        """Validate that all enabled features have their dependencies enabled."""
        for feature, deps in self.DEPENDENCIES.items():
            if self.is_enabled(feature):
                for dep in deps:
                    if not self.is_enabled(dep):
                        logger.warning(
                            f"Feature '{feature}' enabled but depends on '{dep}' "
                            f"(disabled). Disabling '{feature}' to maintain invariant."
                        )
                        self.features[feature] = False

    def is_enabled(self, feature: str) -> bool:
        """
        Check if feature is enabled.

        Args:
            feature: Feature name (e.g., "vibe_engineering_v0_2")

        Returns:
            True if enabled, False otherwise.
        """
        if feature not in self.DEFAULTS:
            logger.error(f"Unknown feature: {feature}")
            return False

        enabled = self.features.get(feature, False)

        if enabled:
            # Check dependencies again (in case state changed)
            deps = self.DEPENDENCIES.get(feature, [])
            for dep in deps:
                if not self.is_enabled(dep):
                    logger.warning(
                        f"Feature '{feature}' requires '{dep}' "
                        f"(currently disabled). Treating as disabled."
                    )
                    return False

        return enabled

    def get_all(self) -> Dict[str, bool]:
        """Get all feature flags and their current state."""
        return self.features.copy()

    def set_feature(self, feature: str, enabled: bool):
        """
        Dynamically enable/disable a feature.

        Args:
            feature: Feature name
            enabled: New state
        """
        if feature not in self.DEFAULTS:
            logger.error(f"Unknown feature: {feature}")
            return

        self.features[feature] = enabled
        self._validate_dependencies()

        level = "enabled" if enabled else "disabled"
        logger.info(f"Feature '{feature}' {level}")


# Global singleton resolver
_global_resolver: Optional[FeatureFlagResolver] = None


def get_resolver() -> FeatureFlagResolver:
    """Get or create global feature flag resolver."""
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = FeatureFlagResolver()
    return _global_resolver


def is_vibe_v0_2_enabled() -> bool:
    """Check if Vibe Engineering v0.2 is enabled."""
    return get_resolver().is_enabled("vibe_engineering_v0_2")


def is_encryption_enabled() -> bool:
    """Check if checkpoint encryption is enabled."""
    return get_resolver().is_enabled("vibe_engineering_encryption")


def is_ml_classifiers_enabled() -> bool:
    """Check if ML classifiers are enabled."""
    return get_resolver().is_enabled("vibe_engineering_ml_classifiers")


def is_dashboard_enabled() -> bool:
    """Check if monitoring dashboard is enabled."""
    return get_resolver().is_enabled("vibe_engineering_monitoring_dashboard")
