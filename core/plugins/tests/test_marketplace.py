"""
Tests for plugin marketplace and governance.

Covers:
- Plugin metadata immutability
- Installation tracking
- Review system and rating recalculation
- Governance rules (auto-removal on low rating)
- Search and filtering
"""

import pytest
from datetime import datetime, timedelta

from core.plugins.marketplace import (
    PluginMetadata,
    PluginInstallation,
    PluginReview,
    PluginRevenue,
    PluginMarketplace,
    PluginCategory,
    PluginOrigin,
    BootLayer,
)


class TestPluginMetadata:
    """Test plugin metadata."""

    def test_metadata_creation(self):
        """Create plugin metadata."""
        plugin = PluginMetadata(
            plugin_id="test-auth-v1",
            name="Test Auth",
            version="1.0.0",
            category=PluginCategory.AUTHENTICATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author-1",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test authentication plugin",
            long_description="Longer description...",
        )
        assert plugin.plugin_id == "test-auth-v1"
        assert plugin.origin == PluginOrigin.COMMUNITY

    def test_metadata_is_frozen(self):
        """Plugin metadata is immutable."""
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            plugin.name = "New Name"

    def test_metadata_to_dict(self):
        """Metadata serializes to dict."""
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test Plugin",
            version="1.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.VETTED,
            author_id="author",
            author_email="author@example.com",
            license="MIT",
            description="A test",
            long_description="Long test",
            rating_average=4.5,
            download_count=100,
        )
        d = plugin.to_dict()
        assert d["plugin_id"] == "test"
        assert d["name"] == "Test Plugin"
        assert d["rating"] == 4.5

    def test_metadata_is_discoverable_if_listed(self):
        """Discoverable only if listed and not builtin."""
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
            listed=True,
        )
        assert plugin.is_discoverable() is True

    def test_metadata_not_discoverable_if_unlisted(self):
        """Not discoverable if unlisted."""
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
            listed=False,
        )
        assert plugin.is_discoverable() is False

    def test_builtin_plugins_are_discoverable_when_listed(self):
        """ADR-0511: a LISTED builtin is discoverable (admin install flow).

        The old expectation (builtin → never discoverable) predates the
        marketplace indexing the bundled ``buildin/`` hierarchy; ``listed`` is
        the one switch, for every origin.
        """
        plugin = PluginMetadata(
            plugin_id="builtin-test",
            name="Builtin Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.BUNDLED,
            origin=PluginOrigin.BUILTIN,
            author_id="corvin",
            author_email="corvin@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
            listed=True,
        )
        assert plugin.is_discoverable() is True


class TestPluginInstallation:
    """Test plugin installation records."""

    def test_installation_creation(self):
        """Create installation record."""
        inst = PluginInstallation(
            installation_id="inst-1",
            operator_id="op-1",
            tenant_id="default",
            plugin_id="test-plugin",
            version="1.0.0",
            enabled=True,
            installed_at=datetime.utcnow(),
        )
        assert inst.installation_id == "inst-1"
        assert inst.enabled is True

    def test_installation_validates_resource_limits(self):
        """Installation validates resource limits."""
        with pytest.raises(ValueError, match="CPU limit"):
            PluginInstallation(
                installation_id="inst-1",
                operator_id="op-1",
                tenant_id="default",
                plugin_id="test",
                version="1.0.0",
                enabled=True,
                installed_at=datetime.utcnow(),
                cpu_limit_percent=101,  # Invalid
            )

    def test_installation_is_frozen(self):
        """Installation records are immutable."""
        inst = PluginInstallation(
            installation_id="inst-1",
            operator_id="op-1",
            tenant_id="default",
            plugin_id="test",
            version="1.0.0",
            enabled=True,
            installed_at=datetime.utcnow(),
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            inst.enabled = False


class TestPluginReview:
    """Test plugin reviews."""

    def test_review_creation(self):
        """Create review."""
        review = PluginReview(
            review_id="rev-1",
            plugin_id="test-plugin",
            operator_id="op-1",
            tenant_id="default",
            rating=5,
            comment="Great plugin!",
        )
        assert review.rating == 5
        assert review.comment == "Great plugin!"

    def test_review_validates_rating(self):
        """Review rating must be in [1..5]."""
        with pytest.raises(ValueError, match="Rating must be in"):
            PluginReview(
                review_id="rev-1",
                plugin_id="test",
                operator_id="op-1",
                tenant_id="default",
                rating=6,  # Invalid
            )

    def test_review_validates_comment_length(self):
        """Comment must be ≤500 chars."""
        long_comment = "x" * 501
        with pytest.raises(ValueError, match="Comment must be"):
            PluginReview(
                review_id="rev-1",
                plugin_id="test",
                operator_id="op-1",
                tenant_id="default",
                rating=4,
                comment=long_comment,
            )


class TestPluginRevenue:
    """Test revenue sharing records."""

    def test_revenue_creation(self):
        """Create revenue record."""
        revenue = PluginRevenue(
            revenue_id="rev-1",
            plugin_id="test-plugin",
            author_id="author-1",
            tenant_id="default",
            period_month="2026-08",
            total_installs=100,
            total_usage_hours=1000.0,
            revenue_usd=500.0,
            author_payout_usd=350.0,
            corvin_payout_usd=100.0,
            ecosystem_payout_usd=50.0,
        )
        assert revenue.revenue_usd == 500.0
        assert revenue.author_payout_usd == 350.0

    def test_revenue_validates_shares_sum_to_one(self):
        """Revenue shares must sum to 1.0."""
        with pytest.raises(ValueError, match="sum to 1.0"):
            PluginRevenue(
                revenue_id="rev-1",
                plugin_id="test",
                author_id="author",
                tenant_id="default",
                period_month="2026-08",
                total_installs=10,
                total_usage_hours=100.0,
                revenue_usd=100.0,
                author_percent=0.5,  # Sum < 1.0
                corvin_percent=0.3,
                ecosystem_percent=0.1,
            )


class TestPluginMarketplace:
    """Test marketplace operations."""

    def test_marketplace_creation(self):
        """Create marketplace.

        ADR-0511: a default-constructed marketplace PRELOADS the plugins it
        discovers under ``buildin/`` + ``contributor/`` (15 at the time of
        writing), so it is never empty on this tree. Pin the preload's shape
        (every entry has a provenance the loader assigned) rather than the
        exact count, which moves with every plugin added.
        """
        mp = PluginMarketplace()
        assert mp is not None
        assert len(mp.plugins) > 0, "ADR-0511 directory discovery preloads the index"
        assert {p.origin for p in mp.plugins.values()} <= {PluginOrigin.BUILTIN, PluginOrigin.COMMUNITY}

    def test_register_plugin(self):
        """Register plugin in marketplace."""
        mp = PluginMarketplace()
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
        )
        mp.register_plugin(plugin)
        assert "test" in mp.plugins
        assert mp.get_plugin("test") == plugin

    def test_list_plugins_search(self):
        """Search plugins by name."""
        mp = PluginMarketplace()
        for i in range(5):
            plugin = PluginMetadata(
                plugin_id=f"auth-{i}",
                name=f"Auth Plugin {i}",
                version="1.0.0",
                category=PluginCategory.AUTHENTICATION,
                boot_layer=BootLayer.INSTALLED,
                origin=PluginOrigin.COMMUNITY,
                author_id="author",
                author_email="author@example.com",
                license="Apache-2.0",
                description="Test",
                long_description="Test",
            )
            mp.register_plugin(plugin)

        results = mp.list_plugins(query="auth")
        assert len(results) >= 3

    def test_record_review_and_recalculate_rating(self):
        """Recording review recalculates plugin rating."""
        mp = PluginMarketplace()
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
        )
        mp.register_plugin(plugin)

        # Record reviews
        for i in range(5):
            review = PluginReview(
                review_id=f"rev-{i}",
                plugin_id="test",
                operator_id=f"op-{i}",
                tenant_id="default",
                rating=i + 1,  # Ratings: 1, 2, 3, 4, 5
            )
            mp.record_review(review)

        # Rating should be 3.0 (average of 1-5)
        plugin = mp.get_plugin("test")
        assert plugin.rating_count == 5
        assert abs(plugin.rating_average - 3.0) < 0.1

    def test_governance_auto_remove_low_rating(self):
        """Plugin with <2 stars auto-removed."""
        mp = PluginMarketplace()
        plugin = PluginMetadata(
            plugin_id="bad-plugin",
            name="Bad Plugin",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
            rating_average=1.5,  # Low rating
            rating_count=10,  # Enough reviews
        )
        mp.register_plugin(plugin)

        # Check governance
        to_remove = mp.check_governance()
        assert "bad-plugin" in to_remove

    def test_remove_plugin_marks_unlisted(self):
        """Removing plugin marks it as unlisted."""
        mp = PluginMarketplace()
        plugin = PluginMetadata(
            plugin_id="test",
            name="Test",
            version="1.0.0",
            category=PluginCategory.TOOLING,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="Test",
            long_description="Test",
            listed=True,
        )
        mp.register_plugin(plugin)

        mp.remove_plugin("test")
        plugin = mp.get_plugin("test")
        assert plugin.listed is False
