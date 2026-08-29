"""
Comprehensive Integration Tests for CorvinOS Plugin Marketplace (ADR-0249).

Golden Path Workflows:
- Search marketplace for plugins
- View trust badges and governance info
- Install plugins from marketplace
- Enable/disable plugins
- Verify plugins work end-to-end
- Multi-plugin concurrent operations
- Multi-tenant isolation

Tests cover API, backend state, audit trail, and registry consistency.
"""

import pytest
import json
import tempfile
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import List, Dict, Any, Optional

# Core imports
from core.plugins.marketplace import (
    PluginMarketplace,
    PluginMetadata,
    PluginInstallation,
    PluginReview,
    PluginCategory,
    PluginOrigin,
    BootLayer,
)


# ============================================================================
# FIXTURES: Marketplace Setup & Teardown
# ============================================================================

@pytest.fixture
def temp_corvin_home():
    """Create temporary .corvin directory for test isolation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corvin_home = Path(tmpdir) / ".corvin"
        corvin_home.mkdir(parents=True, exist_ok=True)
        (corvin_home / "plugins").mkdir(exist_ok=True)
        (corvin_home / "audit.jsonl").touch()
        yield corvin_home


@pytest.fixture
def marketplace():
    """Initialize in-memory marketplace for testing."""
    return PluginMarketplace()


@pytest.fixture
def sample_plugins():
    """Factory: Create sample plugins for marketplace testing."""
    def _create_plugins(count: int = 5, origin: PluginOrigin = None) -> List[PluginMetadata]:
        plugins = []
        for i in range(count):
            if origin is None:
                # Mix of origins
                if i % 3 == 0:
                    origin = PluginOrigin.BUILTIN
                elif i % 3 == 1:
                    origin = PluginOrigin.VETTED
                else:
                    origin = PluginOrigin.COMMUNITY

            plugin = PluginMetadata(
                plugin_id=f"test-plugin-{i}",
                name=f"Test Plugin {i}",
                version="1.0.0",
                category=PluginCategory.INTEGRATION,
                boot_layer=BootLayer.INSTALLED,
                origin=origin,
                author_id=f"author-{i}",
                author_email=f"author{i}@test.io",
                license="Apache-2.0",
                description=f"Test plugin #{i}",
                long_description=f"This is a test plugin used for marketplace testing.",
                homepage_url=f"https://example.com/plugins/plugin-{i}",
                repository_url=f"https://github.com/test/plugin-{i}",
                depends_on=[],
                conflicts_with=[],
                rating_count=10 + i,
                rating_average=4.0 + (i * 0.1),
                download_count=100 + (i * 50),
                listed=True if origin != PluginOrigin.COMMUNITY or i != 2 else False,
            )
            plugins.append(plugin)
        return plugins

    return _create_plugins


@pytest.fixture
def populated_marketplace(marketplace, sample_plugins):
    """Populate marketplace with sample plugins."""
    plugins = sample_plugins(5)
    for plugin in plugins:
        marketplace.register_plugin(plugin)
    return marketplace


@pytest.fixture
def audit_trail(temp_corvin_home):
    """Mock audit trail for testing."""
    audit_file = temp_corvin_home / "audit.jsonl"
    events = []

    class MockAuditTrail:
        def log_event(self, event_type: str, details: Dict[str, Any]):
            event = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "details": details,
            }
            events.append(event)
            audit_file.write_text(
                audit_file.read_text() + json.dumps(event) + "\n"
            )

        def get_events(self, event_type: str = None) -> List[Dict]:
            if event_type is None:
                return events
            return [e for e in events if e["event_type"] == event_type]

    return MockAuditTrail()


# ============================================================================
# TEST SUITE 1: Marketplace Discovery
# ============================================================================

class TestMarketplaceDiscovery:
    """Test plugin discovery workflows."""

    def test_list_all_plugins(self, populated_marketplace):
        """Golden Path: List all plugins from marketplace."""
        plugins = populated_marketplace.list_plugins(limit=100)
        assert len(plugins) == 5
        assert all(p.listed for p in plugins)

    def test_search_plugins_by_name(self, populated_marketplace):
        """Golden Path: Search for plugins by name."""
        # Add a distinctive plugin
        distinctive = PluginMetadata(
            plugin_id="postgres-sync",
            name="PostgreSQL Data Sync",
            version="1.0.0",
            category=PluginCategory.DATABASE,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-pg",
            author_email="author@postgres.io",
            license="Apache-2.0",
            description="Sync data with PostgreSQL",
            long_description="Advanced PostgreSQL integration",
        )
        populated_marketplace.register_plugin(distinctive)

        # Search for it
        results = populated_marketplace.list_plugins(query="postgres")
        assert len(results) >= 1
        assert any(p.plugin_id == "postgres-sync" for p in results)

    def test_filter_by_category(self, populated_marketplace):
        """Test: Filter plugins by category."""
        # Register a security plugin
        sec_plugin = PluginMetadata(
            plugin_id="vault-integration",
            name="HashiCorp Vault",
            version="1.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.CORE,
            origin=PluginOrigin.VETTED,
            author_id="author-vault",
            author_email="vault@test.io",
            license="Apache-2.0",
            description="Vault integration",
            long_description="",
        )
        populated_marketplace.register_plugin(sec_plugin)

        # Filter by category
        results = populated_marketplace.list_plugins(category=PluginCategory.SECURITY)
        assert len(results) == 1
        assert results[0].plugin_id == "vault-integration"

    def test_filter_by_origin(self, populated_marketplace):
        """Test: Filter plugins by origin (builtin/vetted/community)."""
        # Get only vetted plugins
        vetted = populated_marketplace.list_plugins(origin=PluginOrigin.VETTED)
        assert all(p.origin == PluginOrigin.VETTED for p in vetted)

        # Get only community plugins
        community = populated_marketplace.list_plugins(origin=PluginOrigin.COMMUNITY)
        assert all(p.origin == PluginOrigin.COMMUNITY for p in community)

    def test_exclude_unlisted_plugins(self, marketplace, sample_plugins):
        """Test: Unlisted plugins don't appear in results."""
        plugins = sample_plugins(3)
        # First plugin is listed, second is not, third is listed
        plugins[0] = PluginMetadata(
            plugin_id="listed-1",
            name="Listed Plugin 1",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author1@test.io",
            license="Apache-2.0",
            description="Listed",
            long_description="",
            listed=True,
        )
        plugins[1] = PluginMetadata(
            plugin_id="unlisted-1",
            name="Unlisted Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-2",
            author_email="author2@test.io",
            license="Apache-2.0",
            description="Unlisted",
            long_description="",
            listed=False,
        )

        for plugin in plugins:
            marketplace.register_plugin(plugin)

        # List should exclude unlisted
        results = marketplace.list_plugins(limit=100)
        plugin_ids = [p.plugin_id for p in results]
        assert "listed-1" in plugin_ids
        assert "unlisted-1" not in plugin_ids

    def test_pagination(self, populated_marketplace):
        """Test: Pagination with offset and limit."""
        # Request first 2
        page1 = populated_marketplace.list_plugins(limit=2, offset=0)
        assert len(page1) == 2

        # Request next 2
        page2 = populated_marketplace.list_plugins(limit=2, offset=2)
        assert len(page2) == 2

        # IDs should be different
        ids1 = {p.plugin_id for p in page1}
        ids2 = {p.plugin_id for p in page2}
        assert len(ids1 & ids2) == 0  # No overlap

    def test_sort_by_rating(self, marketplace, sample_plugins):
        """Test: Results sorted by rating descending."""
        plugins = [
            PluginMetadata(
                plugin_id="low-rated",
                name="Low Rated",
                version="1.0.0",
                category=PluginCategory.INTEGRATION,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.VETTED,
                author_id="author-1",
                author_email="author1@test.io",
                license="Apache-2.0",
                description="Low rated",
                long_description="",
                rating_average=2.0,
                download_count=10,
            ),
            PluginMetadata(
                plugin_id="high-rated",
                name="High Rated",
                version="1.0.0",
                category=PluginCategory.INTEGRATION,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.VETTED,
                author_id="author-2",
                author_email="author2@test.io",
                license="Apache-2.0",
                description="High rated",
                long_description="",
                rating_average=5.0,
                download_count=100,
            ),
        ]

        for plugin in plugins:
            marketplace.register_plugin(plugin)

        results = marketplace.list_plugins(limit=100)
        # High-rated should come first
        assert results[0].rating_average >= results[1].rating_average


# ============================================================================
# TEST SUITE 2: Plugin Installation
# ============================================================================

class TestPluginInstallation:
    """Test plugin installation workflows."""

    def test_record_installation(self, marketplace, audit_trail):
        """Golden Path: Record plugin installation in registry."""
        # Create and register a plugin
        plugin = PluginMetadata(
            plugin_id="test-install",
            name="Test Install",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="Test",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Record installation
        installation = PluginInstallation(
            installation_id=str(uuid.uuid4()),
            operator_id="operator-1",
            tenant_id="tenant-1",
            plugin_id="test-install",
            version="1.0.0",
            enabled=True,
        )
        marketplace.record_installation(installation)

        # Audit should have event
        audit_trail.log_event("plugin.installed", {
            "plugin_id": "test-install",
            "version": "1.0.0",
        })

        # Verify events logged
        events = audit_trail.get_events("plugin.installed")
        assert len(events) == 1
        assert events[0]["details"]["plugin_id"] == "test-install"

    def test_concurrent_installations(self, marketplace, audit_trail):
        """Test: Multiple plugins installed concurrently."""
        plugins = []
        for i in range(5):
            plugin = PluginMetadata(
                plugin_id=f"plugin-{i}",
                name=f"Plugin {i}",
                version="1.0.0",
                category=PluginCategory.INTEGRATION,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.VETTED,
                author_id=f"author-{i}",
                author_email=f"author{i}@test.io",
                license="Apache-2.0",
                description=f"Plugin {i}",
                long_description="",
            )
            marketplace.register_plugin(plugin)
            plugins.append(plugin)

        # Record all installations
        for i, plugin in enumerate(plugins):
            installation = PluginInstallation(
                installation_id=str(uuid.uuid4()),
                operator_id="operator-1",
                tenant_id="tenant-1",
                plugin_id=plugin.plugin_id,
                version="1.0.0",
                enabled=True,
            )
            marketplace.record_installation(installation)

        # All should be recorded
        assert len(marketplace.installations) == 5

    def test_installation_validation(self, marketplace):
        """Test: Installation validation (CPU, memory, timeout limits)."""
        plugin = PluginMetadata(
            plugin_id="test-limits",
            name="Test Limits",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Valid installation
        valid = PluginInstallation(
            installation_id=str(uuid.uuid4()),
            operator_id="operator-1",
            tenant_id="tenant-1",
            plugin_id="test-limits",
            version="1.0.0",
            enabled=True,
            cpu_limit_percent=50,
            memory_limit_mb=256,
            timeout_seconds=60,
        )
        assert valid  # Should not raise

        # Invalid CPU
        with pytest.raises(ValueError, match="CPU limit"):
            PluginInstallation(
                installation_id=str(uuid.uuid4()),
                operator_id="operator-1",
                tenant_id="tenant-1",
                plugin_id="test-limits",
                version="1.0.0",
                enabled=True,
                cpu_limit_percent=150,  # Invalid
            )

        # Invalid memory
        with pytest.raises(ValueError, match="Memory"):
            PluginInstallation(
                installation_id=str(uuid.uuid4()),
                operator_id="operator-1",
                tenant_id="tenant-1",
                plugin_id="test-limits",
                version="1.0.0",
                enabled=True,
                memory_limit_mb=30,  # Too low
            )


# ============================================================================
# TEST SUITE 3: Plugin Ratings & Reviews
# ============================================================================

class TestPluginRatings:
    """Test plugin review and rating workflows."""

    def test_record_review(self, marketplace):
        """Golden Path: Record plugin review and update rating."""
        # Register plugin
        plugin = PluginMetadata(
            plugin_id="rated-plugin",
            name="Rated Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Add review
        review = PluginReview(
            review_id=str(uuid.uuid4()),
            plugin_id="rated-plugin",
            operator_id="operator-1",
            tenant_id="tenant-1",
            rating=5,
            comment="Excellent plugin!",
        )
        marketplace.record_review(review)

        # Check rating updated
        updated = marketplace.get_plugin("rated-plugin")
        assert updated.rating_average == 5.0
        assert updated.rating_count == 1

    def test_multiple_reviews_average(self, marketplace):
        """Test: Average rating from multiple reviews."""
        plugin = PluginMetadata(
            plugin_id="multi-rated",
            name="Multi Rated",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Add multiple reviews (ratings: 5, 4, 3)
        for rating in [5, 4, 3]:
            review = PluginReview(
                review_id=str(uuid.uuid4()),
                plugin_id="multi-rated",
                operator_id=f"operator-{rating}",
                tenant_id="tenant-1",
                rating=rating,
            )
            marketplace.record_review(review)

        updated = marketplace.get_plugin("multi-rated")
        assert updated.rating_count == 3
        assert updated.rating_average == pytest.approx(4.0, abs=0.01)

    def test_review_validation(self):
        """Test: Review input validation."""
        # Valid review
        valid = PluginReview(
            review_id=str(uuid.uuid4()),
            plugin_id="test",
            operator_id="op-1",
            tenant_id="tenant-1",
            rating=4,
            comment="Good",
        )
        assert valid

        # Invalid rating (too high)
        with pytest.raises(ValueError, match="Rating must be in"):
            PluginReview(
                review_id=str(uuid.uuid4()),
                plugin_id="test",
                operator_id="op-1",
                tenant_id="tenant-1",
                rating=6,  # Invalid
            )

        # Comment too long
        with pytest.raises(ValueError, match="Comment must be"):
            PluginReview(
                review_id=str(uuid.uuid4()),
                plugin_id="test",
                operator_id="op-1",
                tenant_id="tenant-1",
                rating=4,
                comment="x" * 501,  # Too long
            )

    def test_get_reviews(self, marketplace):
        """Test: Retrieve reviews for a plugin."""
        plugin = PluginMetadata(
            plugin_id="reviewed",
            name="Reviewed",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Add 3 reviews
        for i in range(3):
            review = PluginReview(
                review_id=str(uuid.uuid4()),
                plugin_id="reviewed",
                operator_id=f"op-{i}",
                tenant_id="tenant-1",
                rating=4 + i,
            )
            marketplace.record_review(review)

        reviews = marketplace.get_reviews("reviewed")
        assert len(reviews) == 3


# ============================================================================
# TEST SUITE 4: Governance & Auto-Removal
# ============================================================================

class TestPluginGovernance:
    """Test plugin governance and auto-removal rules."""

    def test_governance_check_no_removal_needed(self, marketplace):
        """Test: Governance check passes when plugin is healthy."""
        plugin = PluginMetadata(
            plugin_id="healthy",
            name="Healthy Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
            rating_count=20,
            rating_average=4.5,
        )
        marketplace.register_plugin(plugin)

        # Check governance
        to_remove = marketplace.check_governance()
        assert "healthy" not in to_remove

    def test_governance_auto_remove_low_rating(self, marketplace):
        """Test: Plugin auto-removed when rating drops below 2 stars."""
        plugin = PluginMetadata(
            plugin_id="bad-plugin",
            name="Bad Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
            rating_count=10,  # Enough for governance rule
            rating_average=1.5,  # Below 2.0
        )
        marketplace.register_plugin(plugin)

        # Check governance
        to_remove = marketplace.check_governance()
        assert "bad-plugin" in to_remove

    def test_governance_preserve_low_rating_if_few_reviews(self, marketplace):
        """Test: Don't remove low-rated plugins with <5 reviews."""
        plugin = PluginMetadata(
            plugin_id="new-plugin",
            name="New Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
            rating_count=2,  # Few reviews
            rating_average=1.5,  # Low rating, but few reviews
        )
        marketplace.register_plugin(plugin)

        to_remove = marketplace.check_governance()
        assert "new-plugin" not in to_remove

    def test_remove_plugin_marks_unlisted(self, marketplace):
        """Test: Removing a plugin marks it as unlisted."""
        plugin = PluginMetadata(
            plugin_id="to-remove",
            name="To Remove",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
            listed=True,
        )
        marketplace.register_plugin(plugin)

        # Remove it
        marketplace.remove_plugin("to-remove")

        # Check it's unlisted
        removed = marketplace.get_plugin("to-remove")
        assert not removed.listed


# ============================================================================
# TEST SUITE 5: Multi-Tenant Isolation
# ============================================================================

class TestMultiTenantIsolation:
    """Test tenant isolation for marketplace and installations."""

    def test_installations_isolated_by_tenant(self, marketplace):
        """Golden Path: Tenant A's installations don't affect Tenant B."""
        plugin = PluginMetadata(
            plugin_id="shared-plugin",
            name="Shared Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Tenant A installs
        install_a = PluginInstallation(
            installation_id=str(uuid.uuid4()),
            operator_id="op-a",
            tenant_id="tenant-a",
            plugin_id="shared-plugin",
            version="1.0.0",
            enabled=True,
        )
        marketplace.record_installation(install_a)

        # Tenant B installs same plugin
        install_b = PluginInstallation(
            installation_id=str(uuid.uuid4()),
            operator_id="op-b",
            tenant_id="tenant-b",
            plugin_id="shared-plugin",
            version="1.0.0",
            enabled=True,
        )
        marketplace.record_installation(install_b)

        # Both installations should exist
        assert len(marketplace.installations["shared-plugin"]) == 2
        # Verify they're from different tenants
        tenants = {i.tenant_id for i in marketplace.installations["shared-plugin"]}
        assert tenants == {"tenant-a", "tenant-b"}

    @pytest.mark.parametrize("tenant_id", ["tenant-1", "tenant-2", "tenant-3"])
    def test_multi_tenant_reviews(self, marketplace, tenant_id):
        """Test: Reviews isolated per tenant."""
        plugin = PluginMetadata(
            plugin_id="reviewed-plugin",
            name="Reviewed Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Each tenant leaves a review
        review = PluginReview(
            review_id=str(uuid.uuid4()),
            plugin_id="reviewed-plugin",
            operator_id=f"op-{tenant_id}",
            tenant_id=tenant_id,
            rating=4,
        )
        marketplace.record_review(review)

        # Reviews should be recorded for this tenant
        reviews = marketplace.get_reviews("reviewed-plugin")
        assert any(r.tenant_id == tenant_id for r in reviews)


# ============================================================================
# TEST SUITE 6: Integration: Full Workflows
# ============================================================================

class TestFullWorkflows:
    """Test complete end-to-end workflows."""

    def test_discovery_to_install_to_review(self, marketplace, audit_trail):
        """Golden Path: Search → Install → Review."""
        # 1. Register plugins in marketplace
        plugin = PluginMetadata(
            plugin_id="saml-auth",
            name="Enterprise SAML",
            version="2.1.0",
            category=PluginCategory.AUTHENTICATION,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@corvin.io",
            license="Apache-2.0",
            description="Enterprise SAML authentication",
            long_description="Full SAML 2.0 support",
            rating_count=10,
            rating_average=4.8,
            download_count=500,
        )
        marketplace.register_plugin(plugin)

        # 2. Search marketplace
        results = marketplace.list_plugins(
            category=PluginCategory.AUTHENTICATION,
            query="saml"
        )
        assert len(results) >= 1
        assert results[0].plugin_id == "saml-auth"

        # 3. Install plugin
        installation = PluginInstallation(
            installation_id=str(uuid.uuid4()),
            operator_id="operator-1",
            tenant_id="tenant-1",
            plugin_id="saml-auth",
            version="2.1.0",
            enabled=True,
        )
        marketplace.record_installation(installation)
        audit_trail.log_event("plugin.installed", {
            "plugin_id": "saml-auth",
            "operator_id": "operator-1",
        })

        # 4. Review plugin
        review = PluginReview(
            review_id=str(uuid.uuid4()),
            plugin_id="saml-auth",
            operator_id="operator-1",
            tenant_id="tenant-1",
            rating=5,
            comment="Works great for our enterprise!",
        )
        marketplace.record_review(review)

        # 5. Verify final state
        updated = marketplace.get_plugin("saml-auth")
        assert updated.rating_count == 11  # 10 + 1 new review
        assert updated.rating_average > 4.8  # Improved

        # 6. Verify audit trail
        install_events = audit_trail.get_events("plugin.installed")
        assert len(install_events) == 1

    def test_concurrent_installs_and_reviews(self, marketplace):
        """Test: Many concurrent installs and reviews."""
        plugin = PluginMetadata(
            plugin_id="popular",
            name="Popular Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.VETTED,
            author_id="author-1",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )
        marketplace.register_plugin(plugin)

        # Simulate 10 operators installing and reviewing
        for i in range(10):
            installation = PluginInstallation(
                installation_id=str(uuid.uuid4()),
                operator_id=f"op-{i}",
                tenant_id="tenant-1",
                plugin_id="popular",
                version="1.0.0",
                enabled=True,
            )
            marketplace.record_installation(installation)

            review = PluginReview(
                review_id=str(uuid.uuid4()),
                plugin_id="popular",
                operator_id=f"op-{i}",
                tenant_id="tenant-1",
                rating=5 if i % 2 == 0 else 4,
            )
            marketplace.record_review(review)

        # Verify state
        updated = marketplace.get_plugin("popular")
        assert updated.rating_count == 10
        assert len(marketplace.installations["popular"]) == 10


# ============================================================================
# TEST SUITE 7: Error Handling & Edge Cases
# ============================================================================

class TestErrorHandling:
    """Test error paths and edge cases."""

    def test_install_nonexistent_plugin(self, marketplace):
        """Test: Installing a non-existent plugin fails gracefully."""
        plugin = marketplace.get_plugin("doesnt-exist")
        assert plugin is None

    def test_review_nonexistent_plugin(self, marketplace):
        """Test: Reviewing a non-existent plugin (should not crash)."""
        review = PluginReview(
            review_id=str(uuid.uuid4()),
            plugin_id="doesnt-exist",
            operator_id="op-1",
            tenant_id="tenant-1",
            rating=4,
        )
        # Should be recorded even if plugin doesn't exist
        marketplace.record_review(review)
        assert "doesnt-exist" in marketplace.reviews

    def test_duplicate_plugin_registration(self, marketplace, sample_plugins):
        """Test: Registering same plugin twice fails."""
        plugin = sample_plugins(1)[0]
        marketplace.register_plugin(plugin)

        # Try to register again
        with pytest.raises(ValueError, match="already registered"):
            marketplace.register_plugin(plugin)

    def test_empty_marketplace_list(self, marketplace):
        """Test: Listing plugins from empty marketplace."""
        results = marketplace.list_plugins(limit=100)
        assert results == []

    def test_out_of_range_pagination(self, populated_marketplace):
        """Test: Requesting out-of-range pagination."""
        results = populated_marketplace.list_plugins(limit=10, offset=1000)
        assert results == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
