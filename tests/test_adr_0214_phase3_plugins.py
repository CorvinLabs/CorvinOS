"""E2E Tests: ADR-0214 Phase 3 Detector Plugin Registry.

Tests plugin registration, signature validation, and CLS-tier gating.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.detector_plugin_registry import (
    DetectorPluginRegistry,
    Ed25519SignatureValidator,
    get_plugin_registry,
    reset_plugin_registry,
)


class MockDetector:
    """Mock detector plugin for testing."""

    async def detect_engine(self, task, context, initial_analysis):
        """Mock implementation."""
        return ("tiered_delegation", 0.75, {"parallelization_ratio": 0.5})


def test_plugin_registry_basic_registration():
    """Test basic plugin registration without signature."""
    registry = DetectorPluginRegistry(cls_tier="free")

    metadata = {
        "name": "mock_detector",
        "version": "1.0.0",
        "author": "test",
        "cls_tier": "free",
    }

    result = registry.register_plugin(metadata, MockDetector)
    assert result is True, "Registration should succeed"
    assert registry.get_plugin("mock_detector") is not None
    print("✓ Basic registration test PASSED")


def test_plugin_registry_cls_tier_gating():
    """Test CLS tier gating (block enterprise plugin on free tier)."""
    registry = DetectorPluginRegistry(cls_tier="free")

    # Enterprise plugin
    metadata = {
        "name": "enterprise_detector",
        "version": "1.0.0",
        "author": "premium",
        "cls_tier": "enterprise",
    }

    result = registry.register_plugin(metadata, MockDetector)
    assert result is False, "Enterprise plugin should be blocked on free tier"
    print("✓ CLS tier gating test PASSED")


def test_plugin_registry_cls_tier_upgrade():
    """Test that enterprise tier can load enterprise plugins."""
    registry = DetectorPluginRegistry(cls_tier="enterprise")

    metadata = {
        "name": "enterprise_detector",
        "version": "1.0.0",
        "author": "premium",
        "cls_tier": "enterprise",
    }

    result = registry.register_plugin(metadata, MockDetector)
    assert result is True, "Enterprise plugin should load on enterprise tier"
    print("✓ CLS tier upgrade test PASSED")


def test_plugin_registry_invalid_metadata():
    """Test that plugins with incomplete metadata are rejected."""
    registry = DetectorPluginRegistry(cls_tier="free")

    # Missing "author"
    metadata = {
        "name": "incomplete_detector",
        "version": "1.0.0",
        # author missing
        "cls_tier": "free",
    }

    result = registry.register_plugin(metadata, MockDetector)
    assert result is False, "Incomplete metadata should be rejected"
    print("✓ Invalid metadata test PASSED")


def test_plugin_registry_list_and_get():
    """Test listing and retrieving plugins."""
    registry = DetectorPluginRegistry(cls_tier="free")

    # Register multiple plugins
    for i in range(3):
        metadata = {
            "name": f"detector_{i}",
            "version": "1.0.0",
            "author": "test",
            "cls_tier": "free",
        }
        registry.register_plugin(metadata, MockDetector)

    plugins = registry.list_plugins()
    assert len(plugins) == 3
    assert "detector_0" in plugins
    print("✓ List and get test PASSED")


def test_signature_validator_mock():
    """Test signature validation (mock — no real crypto)."""
    validator = Ed25519SignatureValidator()

    # Test with empty signature (should fail gracefully)
    metadata_json = '{"name":"test"}'
    public_key = ""
    signature = ""

    result = validator.verify_signature(metadata_json, public_key, signature)
    assert result is False, "Invalid signature should fail"
    print("✓ Mock signature validation test PASSED (fail-closed)")


def test_global_registry_singleton():
    """Test global registry singleton pattern."""
    reset_plugin_registry()

    reg1 = get_plugin_registry(cls_tier="free")
    reg2 = get_plugin_registry(cls_tier="team")  # Tier ignored on existing singleton

    assert reg1 is reg2, "Should return same singleton"
    print("✓ Singleton pattern test PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("ADR-0214 Phase 3: Detector Plugin Registry Tests")
    print("=" * 60)
    test_plugin_registry_basic_registration()
    test_plugin_registry_cls_tier_gating()
    test_plugin_registry_cls_tier_upgrade()
    test_plugin_registry_invalid_metadata()
    test_plugin_registry_list_and_get()
    test_signature_validator_mock()
    test_global_registry_singleton()
    print("\n✅ ALL PLUGIN REGISTRY TESTS PASSED")
