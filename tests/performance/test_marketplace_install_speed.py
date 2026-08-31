"""
Performance Tests for Marketplace Installation (Phase 4).

Benchmarks plugin installation speed and caching efficiency.
Target: <5 seconds for small plugin installation (<10MB)
"""

import pytest
import time
import json
from unittest.mock import Mock, patch


try:
    from core.plugins.marketplace import PluginMarketplace, PluginMetadata, PluginCategory, PluginOrigin, BootLayer
    from core.console.corvin_console.routes.marketplace_cache import MarketplaceCacheManager
except ImportError:
    pytest.skip("Marketplace modules not available", allow_module_level=True)


@pytest.fixture
def marketplace():
    """Create a PluginMarketplace instance."""
    return PluginMarketplace()


@pytest.fixture
def sample_plugin():
    """Create a sample plugin metadata."""
    return PluginMetadata(
        plugin_id="perf-test-plugin",
        name="Performance Test Plugin",
        version="1.0.0",
        category=PluginCategory.PERFORMANCE,
        boot_layer=BootLayer.INSTALLED,
        origin=PluginOrigin.COMMUNITY,
        author_id="test-author",
        author_email="author@example.com",
        license="Apache-2.0",
        description="A performance test plugin for benchmarking",
        long_description="Used to test installation speed and caching",
    )


class TestInstallationSpeed:
    """Test installation speed benchmarks."""

    def test_plugin_registration_speed(self, marketplace, sample_plugin):
        """Plugin registration should be fast."""
        start = time.time()
        marketplace.register_plugin(sample_plugin)
        elapsed = time.time() - start

        # Registration should be <10ms
        assert elapsed < 0.01, f"Plugin registration took {elapsed:.3f}s (target <0.01s)"

    def test_list_plugins_speed(self, marketplace, sample_plugin):
        """Listing plugins should be fast even with many plugins."""
        # Register 100 plugins
        for i in range(100):
            plugin = PluginMetadata(
                plugin_id=f"plugin-{i}",
                name=f"Plugin {i}",
                version="1.0.0",
                category=PluginCategory.PERFORMANCE,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.COMMUNITY,
                author_id="test-author",
                author_email="author@example.com",
                license="Apache-2.0",
                description=f"Test plugin {i}",
                long_description="",
            )
            marketplace.register_plugin(plugin)

        # List should be fast
        start = time.time()
        results = marketplace.list_plugins(limit=20)
        elapsed = time.time() - start

        assert len(results) <= 20
        # List should be <100ms even with 100 plugins
        assert elapsed < 0.1, f"Plugin listing took {elapsed:.3f}s (target <0.1s)"

    def test_search_speed(self, marketplace):
        """Search should be responsive."""
        # Register 50 plugins with various names
        for i in range(50):
            plugin = PluginMetadata(
                plugin_id=f"auth-plugin-{i}",
                name=f"Authentication Plugin {i}",
                version="1.0.0",
                category=PluginCategory.AUTHENTICATION,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.COMMUNITY,
                author_id="test-author",
                author_email="author@example.com",
                license="Apache-2.0",
                description="Authentication plugin",
                long_description="",
            )
            marketplace.register_plugin(plugin)

        # Search should be fast
        start = time.time()
        results = marketplace.list_plugins(query="auth", limit=20)
        elapsed = time.time() - start

        # Search <100ms target
        assert elapsed < 0.1, f"Search took {elapsed:.3f}s (target <0.1s)"
        assert len(results) > 0


class TestCachingEfficiency:
    """Test caching efficiency."""

    def test_cache_hit_is_fast(self, tmp_path):
        """Cache hits should be significantly faster than cache misses."""
        cache = MarketplaceCacheManager(cache_dir=str(tmp_path))

        sample_data = [
            {
                "id": f"plugin-{i}",
                "name": f"Plugin {i}",
                "rating": 4.5,
            }
            for i in range(100)
        ]

        # First write (cache miss behavior)
        cache.set(sample_data)

        # Measure cache hit
        start = time.time()
        cached = cache.get()
        cache_hit_time = time.time() - start

        # Cache hit should be very fast (<1ms)
        assert cached is not None
        assert cache_hit_time < 0.001, f"Cache hit took {cache_hit_time:.4f}s (target <0.001s)"


class TestMarketplaceResponseTime:
    """Test marketplace API response times."""

    def test_marketplace_index_response_time_cached(self, tmp_path):
        """Cached marketplace index response should be fast."""
        cache = MarketplaceCacheManager(cache_dir=str(tmp_path))

        sample_data = [
            {
                "id": f"plugin-{i}",
                "name": f"Plugin {i}",
                "rating": 4.5 - (i * 0.01),
            }
            for i in range(100)
        ]

        # Warm up cache
        cache.set(sample_data)

        # Measure response time (including serialization)
        start = time.time()
        cached = cache.get()
        response_data = json.dumps(cached, default=str)
        elapsed = time.time() - start

        # Full response <50ms target (cache hit + serialization)
        assert elapsed < 0.05, f"Marketplace index response took {elapsed:.3f}s (target <0.05s)"

    def test_search_response_time(self, marketplace):
        """Search response should be under 500ms."""
        # Register 200 plugins
        for i in range(200):
            plugin = PluginMetadata(
                plugin_id=f"plugin-{i}",
                name=f"Test Plugin {i}",
                version="1.0.0",
                category=PluginCategory.PERFORMANCE if i % 2 == 0 else PluginCategory.SECURITY,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.COMMUNITY,
                author_id="test-author",
                author_email="author@example.com",
                license="Apache-2.0",
                description="Test plugin",
                long_description="",
            )
            marketplace.register_plugin(plugin)

        # Measure search + serialization
        start = time.time()
        results = marketplace.list_plugins(query="Test", limit=20)
        response_data = json.dumps(
            [r.to_dict() for r in results],
            default=str
        )
        elapsed = time.time() - start

        # Search + serialization <500ms target
        assert elapsed < 0.5, f"Search response took {elapsed:.3f}s (target <0.5s)"


class TestInstallationWorkflow:
    """Test end-to-end installation workflow timing."""

    def test_install_workflow_duration(self, marketplace, sample_plugin):
        """Complete install workflow should complete quickly."""
        marketplace.register_plugin(sample_plugin)

        start = time.time()

        # Simulate install workflow steps
        plugin = marketplace.get_plugin("perf-test-plugin")
        assert plugin is not None

        # Record installation (mock)
        from core.plugins.marketplace import PluginInstallation
        install = PluginInstallation(
            installation_id="test-install-1",
            operator_id="operator-1",
            tenant_id="tenant-1",
            plugin_id="perf-test-plugin",
            version="1.0.0",
            enabled=True,
        )
        marketplace.record_installation(install)

        elapsed = time.time() - start

        # Full workflow <100ms
        assert elapsed < 0.1, f"Install workflow took {elapsed:.3f}s (target <0.1s)"


class TestPerformanceSummary:
    """Summary of performance benchmarks."""

    def test_benchmark_report(self, marketplace, tmp_path):
        """Generate benchmark report."""
        benchmarks = {}

        # Test 1: Registration
        plugin = PluginMetadata(
            plugin_id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="",
        )

        start = time.time()
        marketplace.register_plugin(plugin)
        benchmarks["plugin_registration"] = time.time() - start

        # Test 2: List 100 plugins
        for i in range(99):
            p = PluginMetadata(
                plugin_id=f"plugin-{i}",
                name=f"Plugin {i}",
                version="1.0.0",
                category=PluginCategory.PERFORMANCE,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.COMMUNITY,
                author_id="author",
                author_email="author@example.com",
                license="Apache-2.0",
                description="Test",
                long_description="",
            )
            marketplace.register_plugin(p)

        start = time.time()
        marketplace.list_plugins()
        benchmarks["list_plugins_100"] = time.time() - start

        # Test 3: Cache hit
        cache = MarketplaceCacheManager(cache_dir=str(tmp_path))
        sample_data = [{"id": "p1", "name": "Plugin 1"}] * 100
        cache.set(sample_data)

        start = time.time()
        cache.get()
        benchmarks["cache_hit"] = time.time() - start

        # Report
        print("\n" + "=" * 50)
        print("MARKETPLACE PERFORMANCE BENCHMARKS")
        print("=" * 50)
        for name, duration in benchmarks.items():
            print(f"{name:.<40} {duration*1000:.2f}ms")
        print("=" * 50)

        # Verify all pass targets
        assert benchmarks["plugin_registration"] < 0.01
        assert benchmarks["list_plugins_100"] < 0.1
        assert benchmarks["cache_hit"] < 0.001
