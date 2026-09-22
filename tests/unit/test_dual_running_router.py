"""Tests for dual-running request router — canary deployment infrastructure.

ADR-0039 Phase 6: Dual-running + migration infrastructure.
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from corvin_console.routes.dual_running import (
    DualRunningRouter, Cohort, route_workflows_request, enroll_tenant_in_cohort, get_router
)


class TestDualRunningRouterBasic:
    """Test basic router initialization and routing logic."""

    def test_router_init(self, tmp_path):
        """Router initializes with config directory."""
        router = DualRunningRouter(config_dir=str(tmp_path))
        assert router.config_dir == tmp_path
        assert tmp_path.exists()

    def test_router_init_creates_config_dir(self, tmp_path):
        """Router creates config directory if missing."""
        config_dir = tmp_path / "workflows"
        assert not config_dir.exists()

        router = DualRunningRouter(config_dir=str(config_dir))

        assert config_dir.exists()

    def test_default_cohort_dir(self):
        """Router defaults to ~/.corvin/tenants/_default/workflows/."""
        router = DualRunningRouter()
        expected_dir = Path.home() / ".corvin" / "tenants" / "_default" / "workflows"
        assert router.config_dir == expected_dir


class TestDualRunningRouterRouting:
    """Test request routing by feature flag + cohort."""

    def test_route_feature_flag_off_always_console(self, tmp_path):
        """When feature flag off, route to console regardless of cohort."""
        router = DualRunningRouter(config_dir=str(tmp_path))
        router.set_tenant_cohort("tenant_early", Cohort.EARLY)

        # Feature flag off
        result = router.route_request("tenant_early", feature_flag_enabled=False)
        assert result == "console"

    def test_route_feature_flag_on_early_cohort_plugin(self, tmp_path):
        """Early cohort with feature flag on routes to plugin."""
        router = DualRunningRouter(config_dir=str(tmp_path))
        router.set_tenant_cohort("tenant_early", Cohort.EARLY)

        # Feature flag on
        result = router.route_request("tenant_early", feature_flag_enabled=True)
        assert result == "plugin"

    def test_route_feature_flag_on_staged_cohort_plugin(self, tmp_path):
        """Staged cohort with feature flag on routes to plugin."""
        router = DualRunningRouter(config_dir=str(tmp_path))
        router.set_tenant_cohort("tenant_staged", Cohort.STAGED)

        result = router.route_request("tenant_staged", feature_flag_enabled=True)
        assert result == "plugin"

    def test_route_feature_flag_on_control_cohort_console(self, tmp_path):
        """Control cohort routes to console (default)."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Tenant not enrolled (default is CONTROL)
        result = router.route_request("tenant_control", feature_flag_enabled=True)
        assert result == "console"


class TestDualRunningRouterCohorts:
    """Test cohort management (read/write/cache)."""

    def test_set_tenant_cohort(self, tmp_path):
        """Setting tenant cohort writes to disk."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        router.set_tenant_cohort("tenant_1", Cohort.EARLY)

        # Check file exists
        cohort_file = tmp_path / "tenant_1.cohort.json"
        assert cohort_file.exists()

        # Check content
        with open(cohort_file) as f:
            metadata = json.load(f)
        assert metadata["tenant_id"] == "tenant_1"
        assert metadata["cohort"] == "early"
        assert "enrolled_at" in metadata

    def test_get_tenant_cohort_from_disk(self, tmp_path):
        """Getting tenant cohort reads from disk."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        router.set_tenant_cohort("tenant_1", Cohort.STAGED)

        cohort = router.get_tenant_cohort("tenant_1")
        assert cohort == Cohort.STAGED

    def test_get_tenant_cohort_default_control(self, tmp_path):
        """Unknown tenant defaults to CONTROL cohort."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        cohort = router.get_tenant_cohort("unknown_tenant")
        assert cohort == Cohort.CONTROL

    def test_cohort_caching(self, tmp_path):
        """Repeated calls use cache."""
        router = DualRunningRouter(config_dir=str(tmp_path))
        router.set_tenant_cohort("tenant_1", Cohort.EARLY)

        # First call reads from disk
        cohort1 = router.get_tenant_cohort("tenant_1")
        assert cohort1 == Cohort.EARLY

        # Modify the file
        cohort_file = tmp_path / "tenant_1.cohort.json"
        with open(cohort_file, "w") as f:
            json.dump({"tenant_id": "tenant_1", "cohort": "staged"}, f)

        # Second call uses cache (returns EARLY, not STAGED)
        cohort2 = router.get_tenant_cohort("tenant_1")
        assert cohort2 == Cohort.EARLY

        # Cache is consistent
        assert cohort1 == cohort2


class TestDualRunningRouterBulkOperations:
    """Test bulk operations (list, stats)."""

    def test_list_cohorts_empty(self, tmp_path):
        """list_cohorts returns empty dict when no enrollments."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        cohorts = router.list_cohorts()
        assert cohorts == {}

    def test_list_cohorts_multiple(self, tmp_path):
        """list_cohorts returns all enrolled tenants."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        router.set_tenant_cohort("tenant_1", Cohort.EARLY)
        router.set_tenant_cohort("tenant_2", Cohort.STAGED)
        router.set_tenant_cohort("tenant_3", Cohort.EARLY)

        cohorts = router.list_cohorts()
        assert len(cohorts) == 3
        assert cohorts["tenant_1"] == Cohort.EARLY
        assert cohorts["tenant_2"] == Cohort.STAGED
        assert cohorts["tenant_3"] == Cohort.EARLY

    def test_cohort_stats(self, tmp_path):
        """cohort_stats returns distribution."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        router.set_tenant_cohort("tenant_1", Cohort.EARLY)
        router.set_tenant_cohort("tenant_2", Cohort.EARLY)
        router.set_tenant_cohort("tenant_3", Cohort.STAGED)

        stats = router.cohort_stats()
        assert stats["early"] == 2
        assert stats["staged"] == 1
        assert stats["control"] == 0


class TestDualRunningRouterGlobalSingleton:
    """Test global singleton convenience functions."""

    def test_route_workflows_request_function(self):
        """route_workflows_request() is callable."""
        result = route_workflows_request("test_tenant", feature_flag_enabled=True)
        assert result in ("plugin", "console")

    def test_enroll_tenant_in_cohort_function(self, tmp_path):
        """enroll_tenant_in_cohort() works via global singleton."""
        # Temporarily replace global router config dir
        import corvin_console.routes.dual_running as drt
        old_router = drt._router
        try:
            drt._router = DualRunningRouter(config_dir=str(tmp_path))

            enroll_tenant_in_cohort("tenant_1", "early")

            cohort = get_router().get_tenant_cohort("tenant_1")
            assert cohort == Cohort.EARLY
        finally:
            drt._router = old_router

    def test_enroll_tenant_invalid_cohort(self):
        """enroll_tenant_in_cohort() rejects invalid cohort."""
        with pytest.raises(ValueError) as exc_info:
            enroll_tenant_in_cohort("tenant_1", "invalid")
        assert "Invalid cohort" in str(exc_info.value)

    def test_get_router_returns_singleton(self):
        """get_router() returns global singleton."""
        router1 = get_router()
        router2 = get_router()
        assert router1 is router2


class TestDualRunningRouterCanaryScenarios:
    """Test realistic canary deployment scenarios."""

    def test_canary_stage_1_early_adopters(self, tmp_path):
        """Stage 1: 2 early adopter tenants."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Enroll 2 early adopters
        router.set_tenant_cohort("tenant_early_1", Cohort.EARLY)
        router.set_tenant_cohort("tenant_early_2", Cohort.EARLY)

        # Early adopters → plugin
        assert router.route_request("tenant_early_1", feature_flag_enabled=True) == "plugin"
        assert router.route_request("tenant_early_2", feature_flag_enabled=True) == "plugin"

        # Rest → console
        assert router.route_request("tenant_control_1", feature_flag_enabled=True) == "console"
        assert router.route_request("tenant_control_2", feature_flag_enabled=True) == "console"

        # Stats: 2 early, rest control
        stats = router.cohort_stats()
        assert stats["early"] == 2
        assert stats["staged"] == 0
        assert stats["control"] == 0  # Others not enrolled, not in list

    def test_canary_stage_2_staged_rollout(self, tmp_path):
        """Stage 2: 20% of tenants in staged cohort."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Enroll: 2 early + 20 staged (20% of 100)
        for i in range(1, 3):
            router.set_tenant_cohort(f"tenant_early_{i}", Cohort.EARLY)
        for i in range(1, 21):
            router.set_tenant_cohort(f"tenant_staged_{i}", Cohort.STAGED)

        # All enrolled tenants → plugin
        for i in range(1, 3):
            result = router.route_request(f"tenant_early_{i}", feature_flag_enabled=True)
            assert result == "plugin"
        for i in range(1, 21):
            result = router.route_request(f"tenant_staged_{i}", feature_flag_enabled=True)
            assert result == "plugin"

        # Rest → console
        assert router.route_request("tenant_control_1", feature_flag_enabled=True) == "console"

        # Stats
        stats = router.cohort_stats()
        assert stats["early"] == 2
        assert stats["staged"] == 20


class TestDualRunningRouterErrorHandling:
    """Test error handling and resilience."""

    def test_corrupted_cohort_file_defaults_control(self, tmp_path):
        """Corrupted cohort file defaults to CONTROL."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Write corrupted file
        cohort_file = tmp_path / "tenant_bad.cohort.json"
        cohort_file.write_text("NOT VALID JSON {")

        # Should default to CONTROL
        cohort = router.get_tenant_cohort("tenant_bad")
        assert cohort == Cohort.CONTROL

    def test_missing_cohort_field_defaults_control(self, tmp_path):
        """Missing 'cohort' field in metadata defaults to CONTROL."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Write metadata without 'cohort' field
        cohort_file = tmp_path / "tenant_missing.cohort.json"
        cohort_file.write_text('{"tenant_id": "tenant_missing"}')

        # Should default to CONTROL
        cohort = router.get_tenant_cohort("tenant_missing")
        assert cohort == Cohort.CONTROL

    def test_permission_error_on_write_logs_error(self, tmp_path):
        """Permission error on cohort write is logged."""
        router = DualRunningRouter(config_dir=str(tmp_path))

        # Make directory read-only
        tmp_path.chmod(0o444)

        try:
            # Should not raise, but log error
            router.set_tenant_cohort("tenant_1", Cohort.EARLY)
            # Cache still gets populated (in-memory)
            assert router.get_tenant_cohort("tenant_1") == Cohort.EARLY
        finally:
            # Restore permissions for cleanup
            tmp_path.chmod(0o755)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
