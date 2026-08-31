"""E2E integration tests: resolver + cache + hardening (Phase 8 k=3)."""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone
import time
from threading import Thread

from core.skills.corvin_skills.resolver import SkillDependencyResolver
from core.skills.corvin_skills.hardening import (
    SkillServiceHardening,
    SkillServiceRateLimiter,
    SkillServiceCircuitBreaker,
)


class TestResolverWithRateLimiter:
    """Integration: resolver + rate limiting."""

    def test_resolve_respects_rate_limit(self):
        """resolve_with_hardening denies requests after rate limit exceeded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "test_skill", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening(rate_limit_per_minute=2)

            # Calls 1–2: should be allowed
            result1 = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="test_skill"
            )
            assert result1 is not None

            result2 = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="test_skill"
            )
            assert result2 is not None

            # Call 3: rate-limited (tokens exhausted)
            result3 = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="test_skill"
            )
            assert result3 is None

    def test_rate_limit_per_client(self):
        """Rate limits are per-client, not global."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "test_skill", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening(rate_limit_per_minute=1)

            # User 1: 1 request (allowed)
            result1 = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="test_skill"
            )
            assert result1 is not None

            # User 2: 1 request (allowed — separate bucket)
            result2 = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_2", skill_name="test_skill"
            )
            assert result2 is not None


class TestResolverWithCircuitBreaker:
    """Integration: resolver + circuit breaker."""

    def test_circuit_breaker_fails_open_on_repeated_errors(self):
        """Circuit breaker opens after failure threshold."""
        hardening = SkillServiceHardening()

        def failing_resolver(skill_name):
            raise RuntimeError("Manifest load failed")

        # Trigger failures until circuit opens
        for i in range(5):
            result = hardening.resolve_with_hardening(
                failing_resolver, client_id="user_1", skill_name="skill"
            )
            assert result is None

        # Circuit should now be OPEN
        assert hardening.circuit_breaker.state == "OPEN"

        # Next request should be rejected without trying
        result = hardening.resolve_with_hardening(
            failing_resolver, client_id="user_1", skill_name="skill"
        )
        assert result is None

    def test_circuit_breaker_half_open_on_recovery_timeout(self):
        """Circuit breaker transitions OPEN → HALF_OPEN after timeout."""
        hardening = SkillServiceHardening(recovery_timeout_seconds=1)

        def failing_resolver(skill_name):
            raise RuntimeError("Manifest load failed")

        # Open the circuit
        for i in range(5):
            hardening.resolve_with_hardening(
                failing_resolver, client_id="user_1", skill_name="skill"
            )

        assert hardening.circuit_breaker.state == "OPEN"

        # Wait for recovery timeout
        time.sleep(1.1)

        # Next request should try (HALF_OPEN)
        assert hardening.circuit_breaker.is_request_allowed()
        assert hardening.circuit_breaker.state == "HALF_OPEN"

    def test_circuit_breaker_closes_on_success_in_half_open(self):
        """Circuit breaker HALF_OPEN → CLOSED on success threshold."""
        hardening = SkillServiceHardening(
            recovery_timeout_seconds=1,
            success_threshold=2,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "test_skill", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            resolver = SkillDependencyResolver(tenant_id="test")

            def failing_then_success_resolver(skill_name):
                raise RuntimeError("Manifest load failed")

            # Open circuit
            for i in range(5):
                hardening.resolve_with_hardening(
                    failing_then_success_resolver,
                    client_id="user_1",
                    skill_name="skill",
                )

            assert hardening.circuit_breaker.state == "OPEN"

            # Wait for recovery
            time.sleep(1.1)

            # Successful requests in HALF_OPEN
            for i in range(2):
                result = hardening.resolve_with_hardening(
                    resolver.resolve,  # Now succeeds
                    client_id="user_1",
                    skill_name="test_skill",
                )
                assert result is not None

            # Should have closed
            assert hardening.circuit_breaker.state == "CLOSED"


class TestCacheInvalidationWithResolver:
    """Integration: cache invalidation triggered by manifest changes."""

    def test_cache_invalidate_on_manifest_write(self):
        """When manifest changes, resolver invalidates cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest1 = {"skills": [{"name": "skill_a", "metadata": {"v": 1}}]}
            manifest_path.write_text(json.dumps(manifest1))

            resolver = SkillDependencyResolver(tenant_id="test")
            stats1 = resolver.stats()
            assert stats1["hits"] == 0
            assert stats1["misses"] == 0

            # First query (cache miss, load from manifest)
            entry1 = resolver.resolve("skill_a")
            assert entry1 is not None
            assert entry1["metadata"]["v"] == 1

            stats2 = resolver.stats()
            assert stats2["misses"] == 1

            # Simulate manifest update
            manifest2 = {"skills": [{"name": "skill_a", "metadata": {"v": 2}}]}
            manifest_path.write_text(json.dumps(manifest2))

            # Invalidate cache (as registry.create() would do)
            resolver.invalidate()

            stats3 = resolver.stats()
            assert stats3["size"] == 0
            assert stats3["invalidations"] == 1

            # Next query should reload from fresh manifest
            entry2 = resolver.resolve("skill_a")
            assert entry2 is not None
            assert entry2["metadata"]["v"] == 2


class TestFullE2EFlow:
    """End-to-end: create resolver, cache hits, hardening gates."""

    def test_full_resolver_lifecycle_with_hardening(self):
        """Complete flow: resolver init → cache hit/miss → rate limit → circuit breaker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [
                    {"name": "skill_a", "metadata": {"type": "bundled"}},
                    {"name": "skill_b", "metadata": {"type": "installed"}},
                ]
            }
            manifest_path.write_text(json.dumps(manifest))

            # Create resolver and hardening
            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening(rate_limit_per_minute=5)

            # Phase 1: Warm cache (misses)
            for skill in ["skill_a", "skill_b"]:
                hardening.resolve_with_hardening(
                    resolver.resolve, client_id="user_1", skill_name=skill
                )

            cache_stats = resolver.stats()
            assert cache_stats["misses"] == 2
            assert cache_stats["hits"] == 0

            # Phase 2: Hit cache (should reuse)
            for skill in ["skill_a", "skill_b"]:
                hardening.resolve_with_hardening(
                    resolver.resolve, client_id="user_1", skill_name=skill
                )

            cache_stats = resolver.stats()
            assert cache_stats["hits"] == 2
            assert cache_stats["hit_rate"] == 0.5

            # Phase 3: Parallel clients with rate limiting
            def worker(client_id, count):
                for i in range(count):
                    hardening.resolve_with_hardening(
                        resolver.resolve,
                        client_id=client_id,
                        skill_name="skill_a",
                    )

            threads = [
                Thread(target=worker, args=(f"user_{i}", 3))
                for i in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Both users should have made requests
            cache_stats = resolver.stats()
            assert cache_stats["hits"] > 2

    def test_health_status_reflects_component_states(self):
        """health_status() accurately reports cache + circuit-breaker state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_a", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening()

            # Warm cache
            hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="skill_a"
            )

            # Check health
            health = hardening.health_status()
            assert health["circuit_breaker"]["state"] == "CLOSED"
            assert health["rate_limiter"]["rate_limit_per_minute"] == 1000
            assert health["timeouts"]["request_seconds"] == 5.0


class TestErrorHandling:
    """Integration: error recovery and graceful degradation."""

    def test_corrupted_manifest_fails_gracefully(self):
        """Corrupted manifest doesn't crash; returns None (circuit breaks)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text("INVALID JSON {{{")

            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening()

            # Should not crash; should return None
            result = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="skill_a"
            )
            assert result is None

            # Circuit breaker should record failure
            state = hardening.circuit_breaker.state_info()
            assert state["failure_count"] > 0

    def test_missing_skill_returns_none_not_error(self):
        """Non-existent skill returns None (not error/exception)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_a", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            resolver = SkillDependencyResolver(tenant_id="test")
            hardening = SkillServiceHardening()

            # Query non-existent skill
            result = hardening.resolve_with_hardening(
                resolver.resolve, client_id="user_1", skill_name="nonexistent"
            )
            assert result is None

            # Should not trip circuit breaker (it was a successful call that returned None)
            state = hardening.circuit_breaker.state_info()
            assert state["state"] == "CLOSED"
