"""
Feature Flag Tests

Test safe feature flag resolution with dependency validation.
"""

import pytest
import os

from core.vibe_engineering.feature_flags import (
    FeatureFlagResolver,
    is_vibe_v0_2_enabled,
    is_encryption_enabled,
    is_ml_classifiers_enabled,
)


class TestFeatureFlagResolver:
    """Test feature flag resolution and dependencies."""

    def test_defaults_all_disabled(self):
        """By default, all features are disabled (safe default)."""
        resolver = FeatureFlagResolver(spec_features={})

        assert resolver.is_enabled("vibe_engineering_v0_2") is False
        assert resolver.is_enabled("vibe_engineering_encryption") is False
        assert resolver.is_enabled("vibe_engineering_ml_classifiers") is False

    def test_explicit_enable_base_feature(self):
        """Explicitly enabling base feature works."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True
        })

        assert resolver.is_enabled("vibe_engineering_v0_2") is True

    def test_dependency_validation_disables_child(self):
        """Enabling child without parent automatically disables child."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": False,  # Parent disabled
            "vibe_engineering_encryption": True  # Child enabled (but parent disabled)
        })

        # Child should be disabled due to missing parent
        assert resolver.is_enabled("vibe_engineering_encryption") is False

    def test_dependency_validation_enables_child(self):
        """Enabling parent allows child to be enabled."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True,
            "vibe_engineering_ml_classifiers": True
        })

        assert resolver.is_enabled("vibe_engineering_v0_2") is True
        assert resolver.is_enabled("vibe_engineering_ml_classifiers") is True

    def test_multiple_dependencies(self):
        """All child features disabled if parent disabled."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": False,
            "vibe_engineering_encryption": True,
            "vibe_engineering_ml_classifiers": True,
            "vibe_engineering_monitoring_dashboard": True
        })

        # Parent disabled, so all children should be too
        assert resolver.is_enabled("vibe_engineering_v0_2") is False
        assert resolver.is_enabled("vibe_engineering_encryption") is False
        assert resolver.is_enabled("vibe_engineering_ml_classifiers") is False
        assert resolver.is_enabled("vibe_engineering_monitoring_dashboard") is False

    def test_enable_parent_then_child(self):
        """Enable parent first, then child works."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True,
            "vibe_engineering_encryption": False
        })

        # Enable encryption (parent already enabled)
        resolver.set_feature("vibe_engineering_encryption", True)
        assert resolver.is_enabled("vibe_engineering_encryption") is True

    def test_disable_parent_disables_child(self):
        """Disabling parent also disables child."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True,
            "vibe_engineering_encryption": True
        })

        # Disable parent
        resolver.set_feature("vibe_engineering_v0_2", False)

        # Child should now be disabled
        assert resolver.is_enabled("vibe_engineering_encryption") is False

    def test_get_all_flags(self):
        """get_all() returns all flags with current state."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True
        })

        all_flags = resolver.get_all()

        assert "vibe_engineering_v0_2" in all_flags
        assert "vibe_engineering_encryption" in all_flags
        assert all_flags["vibe_engineering_v0_2"] is True
        assert all_flags["vibe_engineering_encryption"] is False

    def test_unknown_feature_ignored(self):
        """Querying unknown feature returns False."""
        resolver = FeatureFlagResolver()
        assert resolver.is_enabled("nonexistent_feature") is False

    def test_convenience_functions(self):
        """Test convenience functions."""
        # Create resolver with specific state
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True
        })

        # These would normally use the global resolver, but we're testing the logic
        assert resolver.is_enabled("vibe_engineering_v0_2") is True
        assert resolver.is_enabled("vibe_engineering_encryption") is False

    def test_rollback_procedure_flag_disable(self):
        """Disabling flag is the rollback procedure."""
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True,
            "vibe_engineering_ml_classifiers": True
        })

        # Emergency rollback: disable base feature
        resolver.set_feature("vibe_engineering_v0_2", False)

        # Everything should be disabled now
        assert resolver.is_enabled("vibe_engineering_v0_2") is False
        assert resolver.is_enabled("vibe_engineering_ml_classifiers") is False

    def test_canary_10_percent_simulation(self):
        """Simulate Week 5 canary 10% rollout."""
        # Operator enables for 10% of users
        # In real implementation, this would be tenant-scoped or user-scoped
        resolver = FeatureFlagResolver(spec_features={
            "vibe_engineering_v0_2": True  # Enabled for this user (10% cohort)
        })

        assert resolver.is_enabled("vibe_engineering_v0_2") is True

        # Other user (90%) still has it disabled
        resolver_99_percent = FeatureFlagResolver(spec_features={})
        assert resolver_99_percent.is_enabled("vibe_engineering_v0_2") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
