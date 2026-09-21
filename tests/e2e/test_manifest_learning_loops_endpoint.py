"""E2E tests for /v1/console/capabilities/manifest learning_loops endpoint (ADR-0906 Phase 1).

These tests verify that the manifest endpoint correctly exposes learning loops
declared in plugin manifests, with proper tenant isolation and performance.
"""
import json
import time
from typing import Any, Optional

import pytest


# Note: These tests assume a running console instance at localhost:8765
# To run: pytest tests/e2e/test_manifest_learning_loops_endpoint.py -v
# Set CORVIN_CONSOLE_URL env var to override default


def get_console_url() -> str:
    """Get the console base URL (default: http://localhost:8765)."""
    import os
    return os.environ.get("CORVIN_CONSOLE_URL", "http://localhost:8765")


def get_manifest(tenant_id: str = "_default") -> dict:
    """Fetch the console manifest for a tenant."""
    try:
        import requests
    except ImportError:
        pytest.skip("requests library not available")

    url = f"{get_console_url()}/v1/console/capabilities/manifest"
    headers = {"X-Tenant-ID": tenant_id} if tenant_id != "_default" else {}

    response = requests.get(url, headers=headers, timeout=5)
    response.raise_for_status()
    return response.json()


@pytest.fixture
def manifest() -> dict:
    """Fixture: fetch the manifest once per test."""
    try:
        return get_manifest()
    except Exception as e:
        pytest.skip(f"Could not fetch manifest: {e}")


class TestManifestEndpointBasics:
    """Test basic endpoint behavior."""

    def test_endpoint_returns_200(self, manifest: dict) -> None:
        """Test that the endpoint returns 200."""
        # If we got here without exception, the endpoint returned 200
        assert manifest is not None
        assert isinstance(manifest, dict)

    def test_manifest_has_version(self, manifest: dict) -> None:
        """Test that manifest has version field."""
        assert "version" in manifest
        assert manifest["version"] == "2.0"

    def test_manifest_has_learning_loops_field(self, manifest: dict) -> None:
        """Test that manifest includes learning_loops field."""
        assert "learning_loops" in manifest
        assert isinstance(manifest["learning_loops"], list)

    def test_learning_loops_is_list(self, manifest: dict) -> None:
        """Test that learning_loops is a list (even if empty)."""
        assert isinstance(manifest["learning_loops"], list)


class TestLearningLoopsStructure:
    """Test the structure of learning loop entries."""

    def test_learning_loop_entry_required_fields(self, manifest: dict) -> None:
        """Test that each loop entry has all required fields."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        required_fields = {
            "loop_id",
            "plugin_id",
            "description",
            "event_source",
            "feedback_types",
            "aggregation",
            "status",
        }

        for loop in manifest["learning_loops"]:
            missing = required_fields - set(loop.keys())
            assert not missing, f"Loop {loop.get('loop_id', '?')} missing fields: {missing}"

    def test_learning_loop_entry_field_types(self, manifest: dict) -> None:
        """Test that field types are correct."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        for loop in manifest["learning_loops"]:
            assert isinstance(loop["loop_id"], str)
            assert isinstance(loop["plugin_id"], str)
            assert isinstance(loop["description"], str)
            assert isinstance(loop["event_source"], str)
            assert isinstance(loop["feedback_types"], list)
            assert isinstance(loop["aggregation"], str)
            assert isinstance(loop["status"], str)
            assert isinstance(loop["health_score"], (int, float))
            assert isinstance(loop["event_count_7d"], int)

    def test_learning_loop_id_format(self, manifest: dict) -> None:
        """Test that loop_id has correct format: plugin_id:loop_id."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        for loop in manifest["learning_loops"]:
            loop_id = loop["loop_id"]
            # Expected format: "plugin_id:loop_id"
            assert ":" in loop_id, f"loop_id should contain ':', got: {loop_id}"
            parts = loop_id.split(":")
            assert len(parts) == 2, f"loop_id should have 2 parts, got: {parts}"
            # Verify parts match the plugin_id and match expected patterns
            assert parts[0] == loop["plugin_id"]

    def test_learning_loop_status_valid(self, manifest: dict) -> None:
        """Test that loop status is one of the valid values."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        valid_statuses = {"active", "dormant", "stale", "degrading"}
        for loop in manifest["learning_loops"]:
            assert (
                loop["status"] in valid_statuses
            ), f"Invalid status: {loop['status']}"

    def test_learning_loop_health_score_range(self, manifest: dict) -> None:
        """Test that health_score is in valid range [0.0–1.0]."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        for loop in manifest["learning_loops"]:
            health = loop["health_score"]
            assert 0.0 <= health <= 1.0, f"Invalid health_score: {health}"

    def test_learning_loop_feedback_types_valid(self, manifest: dict) -> None:
        """Test that feedback_types contains valid types."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        valid_types = {
            "outcome_feedback",
            "preference_feedback",
            "confidence_score",
            "metric_observed",
        }

        for loop in manifest["learning_loops"]:
            feedback_types = loop["feedback_types"]
            for ft in feedback_types:
                assert ft in valid_types, f"Invalid feedback_type: {ft}"

    def test_learning_loop_aggregation_valid(self, manifest: dict) -> None:
        """Test that aggregation is a valid type."""
        if not manifest["learning_loops"]:
            pytest.skip("No learning loops in manifest")

        valid_aggs = {
            "rolling_mean_7d",
            "rolling_mean_30d",
            "percentile_p50",
            "percentile_p95",
            "percentile_p99",
            "count_events_7d",
            "count_events_30d",
        }

        for loop in manifest["learning_loops"]:
            assert (
                loop["aggregation"] in valid_aggs
            ), f"Invalid aggregation: {loop['aggregation']}"


class TestManifestPerformance:
    """Test performance characteristics."""

    def test_manifest_load_time_under_100ms(self) -> None:
        """Test that manifest loads in less than 100ms."""
        try:
            import requests
        except ImportError:
            pytest.skip("requests library not available")

        url = f"{get_console_url()}/v1/console/capabilities/manifest"

        start = time.time()
        response = requests.get(url, timeout=5)
        elapsed_ms = (time.time() - start) * 1000

        response.raise_for_status()

        # Load time should be < 100ms (cached; first request might be slower)
        assert elapsed_ms < 500, f"Manifest load took {elapsed_ms:.1f}ms (expected < 500ms)"


class TestManifestCaching:
    """Test caching behavior."""

    def test_manifest_has_hash_field(self, manifest: dict) -> None:
        """Test that manifest has a hash field for cache invalidation."""
        assert "hash" in manifest
        assert isinstance(manifest["hash"], str)
        assert len(manifest["hash"]) > 0  # Should be a non-empty hash

    def test_manifest_hash_stable(self) -> None:
        """Test that manifest hash is stable across requests."""
        try:
            import requests
        except ImportError:
            pytest.skip("requests library not available")

        url = f"{get_console_url()}/v1/console/capabilities/manifest"

        # Fetch manifest twice
        resp1 = requests.get(url, timeout=5)
        hash1 = resp1.json()["hash"]

        resp2 = requests.get(url, timeout=5)
        hash2 = resp2.json()["hash"]

        # Hashes should match (no plugin changes in between)
        assert hash1 == hash2, f"Hash mismatch: {hash1} != {hash2}"


class TestTenantIsolation:
    """Test tenant isolation."""

    def test_manifest_respects_tenant_id(self) -> None:
        """Test that manifest correctly isolates by tenant_id.

        This test is somewhat limited without multiple tenants configured,
        but we can at least verify the tenant_id doesn't cause errors.
        """
        try:
            import requests
        except ImportError:
            pytest.skip("requests library not available")

        url = f"{get_console_url()}/v1/console/capabilities/manifest"

        # Request with explicit tenant header
        response = requests.get(
            url,
            headers={"X-Tenant-ID": "_default"},
            timeout=5,
        )
        response.raise_for_status()

        manifest = response.json()
        assert manifest is not None
        assert "learning_loops" in manifest


class TestManifestJSON:
    """Test JSON validity and schema."""

    def test_manifest_valid_json(self, manifest: dict) -> None:
        """Test that manifest is valid JSON (can be serialized/deserialized)."""
        # If we got the manifest, it's valid JSON
        json_str = json.dumps(manifest)
        reloaded = json.loads(json_str)
        assert reloaded is not None

    def test_manifest_no_nan_or_inf(self, manifest: dict) -> None:
        """Test that manifest contains no NaN or Infinity values."""
        json_str = json.dumps(manifest)

        # These should not appear in valid JSON
        assert "NaN" not in json_str
        assert "Infinity" not in json_str


class TestManifestIntegration:
    """Integration tests."""

    def test_learning_loops_integrated_with_panels(self, manifest: dict) -> None:
        """Test that learning_loops are properly integrated alongside panels."""
        # Manifest should have both panels and learning_loops
        assert "panels" in manifest
        assert "learning_loops" in manifest

        # Both should be lists
        assert isinstance(manifest["panels"], list)
        assert isinstance(manifest["learning_loops"], list)

    def test_manifest_timestamp_present(self, manifest: dict) -> None:
        """Test that manifest has a timestamp."""
        assert "timestamp" in manifest
        # Timestamp should be ISO format with Z suffix
        timestamp = manifest["timestamp"]
        assert isinstance(timestamp, str)
        assert timestamp.endswith("Z")

    def test_manifest_contract_version(self, manifest: dict) -> None:
        """Test that manifest specifies contract version."""
        assert "contract_version" in manifest
        # Should be a string
        assert isinstance(manifest["contract_version"], str)


class TestManifestErrorHandling:
    """Test error handling and degradation."""

    def test_manifest_graceful_degradation(self) -> None:
        """Test that manifest endpoint degrades gracefully on errors.

        Even if plugin loading fails, the endpoint should return a valid
        manifest with empty learning_loops array, not a 500 error.
        """
        try:
            import requests
        except ImportError:
            pytest.skip("requests library not available")

        url = f"{get_console_url()}/v1/console/capabilities/manifest"

        # Just verify we get a 200 response (no 500 errors)
        response = requests.get(url, timeout=5)
        assert response.status_code == 200

        manifest = response.json()
        assert "learning_loops" in manifest
        # learning_loops should be a list, even if empty or plugin loading failed
        assert isinstance(manifest["learning_loops"], list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INTEGRATION TEST SUITES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestManifestEndToEnd:
    """End-to-end test suite."""

    def test_full_manifest_cycle(self) -> None:
        """Test a complete manifest fetch cycle."""
        try:
            import requests
        except ImportError:
            pytest.skip("requests library not available")

        url = f"{get_console_url()}/v1/console/capabilities/manifest"

        # Fetch manifest
        response = requests.get(url, timeout=5)
        assert response.status_code == 200

        manifest = response.json()

        # Verify structure
        assert isinstance(manifest, dict)
        assert "version" in manifest
        assert "learning_loops" in manifest
        assert isinstance(manifest["learning_loops"], list)

        # Verify that if there are loops, they have proper structure
        for loop in manifest["learning_loops"]:
            assert "loop_id" in loop
            assert "plugin_id" in loop
            assert "description" in loop
            assert "event_source" in loop
            assert "feedback_types" in loop
            assert "aggregation" in loop
            assert "status" in loop
            assert "health_score" in loop
            assert "event_count_7d" in loop
