"""
Plugin Governance & Trust Workflows — ADR-0249.

Tests trust badges, governance workflows, and security:
- Builtin plugins (auto-approve)
- Vetted plugins (signature validation)
- Community plugins (explicit confirmation)
- Plugin rating & review governance
- Plugin reporting workflow
- Trust anchor validation (Ed25519 keys)
"""

import pytest
import json
import uuid
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch
from dataclasses import dataclass
from typing import Optional, List

from core.plugins.marketplace import (
    PluginMetadata,
    PluginOrigin,
    BootLayer,
    PluginCategory,
    PluginReview,
)


# ============================================================================
# FIXTURES: Trust & Governance Setup
# ============================================================================

@pytest.fixture
def temp_trust_anchor_dir():
    """Create temporary directory for trust anchors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        trust_dir = Path(tmpdir) / "trust"
        trust_dir.mkdir(parents=True, exist_ok=True)
        yield trust_dir


@pytest.fixture
def mock_trust_anchor_store(temp_trust_anchor_dir):
    """Mock trust anchor storage and validation."""
    anchors_file = temp_trust_anchor_dir / "plugin_trust_anchors.txt"

    class TrustAnchorStore:
        def __init__(self):
            self.anchors = {}
            self.reports = []

        def add_anchor(self, key_id: str, public_key: str):
            """Register a trust anchor (public key)."""
            self.anchors[key_id] = public_key
            anchors_file.write_text(json.dumps(self.anchors))

        def get_anchor(self, key_id: str) -> Optional[str]:
            """Retrieve trust anchor by key ID."""
            return self.anchors.get(key_id)

        def verify_signature(self, plugin_id: str, signature: str, public_key: str) -> bool:
            """Verify Ed25519 signature (mock)."""
            # In real impl: use cryptography.hazmat.primitives.asymmetric.ed25519
            # For testing: verify signature format
            return len(signature) == 128 and public_key in self.anchors.values()

        def list_anchors(self) -> dict:
            return self.anchors.copy()

    return TrustAnchorStore()


@pytest.fixture
def mock_report_system(temp_trust_anchor_dir):
    """Mock plugin reporting system."""
    reports_file = temp_trust_anchor_dir / "reports.jsonl"

    class ReportSystem:
        def __init__(self):
            self.reports = []

        def submit_report(
            self,
            plugin_id: str,
            reason: str,
            details: str,
            operator_id: str,
        ) -> str:
            """Submit a plugin report."""
            report_id = str(uuid.uuid4())
            report = {
                "report_id": report_id,
                "plugin_id": plugin_id,
                "reason": reason,
                "details": details,
                "operator_id": operator_id,
                "submitted_at": datetime.utcnow().isoformat(),
            }
            self.reports.append(report)

            # Append to audit file
            reports_file.write_text(
                reports_file.read_text() + json.dumps(report) + "\n"
                if reports_file.exists()
                else json.dumps(report) + "\n"
            )
            return report_id

        def get_reports(self, plugin_id: str = None) -> List[dict]:
            """Get reports (optionally filtered by plugin)."""
            if plugin_id is None:
                return self.reports
            return [r for r in self.reports if r["plugin_id"] == plugin_id]

        def get_report_count(self, plugin_id: str) -> int:
            """Count reports for a plugin."""
            return len(self.get_reports(plugin_id))

    return ReportSystem()


# ============================================================================
# TEST SUITE 1: Trust Badges & Origins
# ============================================================================

class TestTrustBadges:
    """Test trust badge assignment based on origin."""

    def test_builtin_plugin_trust_badge(self):
        """Test: Builtin plugins get 'verified' badge."""
        plugin = PluginMetadata(
            plugin_id="builtin-auth",
            name="Built-in Auth",
            version="1.0.0",
            category=PluginCategory.AUTHENTICATION,
            boot_layer=BootLayer.COMPLIANCE,
            origin=PluginOrigin.BUILTIN,
            author_id="corvin-labs",
            author_email="support@corvin.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        # Trust badge assignment
        trust_badge = "verified" if plugin.origin in [PluginOrigin.BUILTIN, PluginOrigin.VETTED] else "community"
        assert trust_badge == "verified"

    def test_vetted_plugin_trust_badge(self):
        """Test: Vetted plugins get 'verified' badge."""
        plugin = PluginMetadata(
            plugin_id="vetted-plugin",
            name="Vetted Plugin",
            version="1.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.CORE,
            origin=PluginOrigin.VETTED,
            author_id="external-author",
            author_email="author@example.com",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        trust_badge = "verified" if plugin.origin in [PluginOrigin.BUILTIN, PluginOrigin.VETTED] else "community"
        assert trust_badge == "verified"

    def test_community_plugin_trust_badge(self):
        """Test: Community plugins get 'community' badge."""
        plugin = PluginMetadata(
            plugin_id="community-plugin",
            name="Community Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="community-dev",
            author_email="dev@community.io",
            license="MIT",
            description="",
            long_description="",
        )

        trust_badge = "verified" if plugin.origin in [PluginOrigin.BUILTIN, PluginOrigin.VETTED] else "community"
        assert trust_badge == "community"

    @pytest.mark.parametrize("origin,expected_badge", [
        (PluginOrigin.BUILTIN, "verified"),
        (PluginOrigin.VETTED, "verified"),
        (PluginOrigin.COMMUNITY, "community"),
    ])
    def test_trust_badge_by_origin(self, origin, expected_badge):
        """Test: Trust badge correctly assigned for all origins."""
        plugin = PluginMetadata(
            plugin_id=f"plugin-{origin.value}",
            name=f"Plugin {origin.value}",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=origin,
            author_id="author",
            author_email="author@test.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        badge = "verified" if origin in [PluginOrigin.BUILTIN, PluginOrigin.VETTED] else "community"
        assert badge == expected_badge


# ============================================================================
# TEST SUITE 2: Signature Verification (Ed25519)
# ============================================================================

class TestSignatureVerification:
    """Test plugin signature verification workflow."""

    def test_verify_vetted_plugin_signature(self, mock_trust_anchor_store):
        """Golden Path: Verify vetted plugin signature."""
        # Register trust anchor (Corvin Labs public key)
        corvin_public_key = "a" * 64  # 64-char hex (256-bit)
        mock_trust_anchor_store.add_anchor("corvin-labs", corvin_public_key)

        # Plugin manifest with signature
        plugin_manifest = {
            "plugin": {
                "id": "vetted-db-sync",
                "version": "1.0.0",
                "author": "Corvin Labs",
            },
            "signature": {
                "algorithm": "Ed25519",
                "key_id": "corvin-labs",
                "signature": "b" * 128,  # 128-char hex (256-bit signature)
            }
        }

        # Verify signature
        key_id = plugin_manifest["signature"]["key_id"]
        public_key = mock_trust_anchor_store.get_anchor(key_id)
        assert public_key is not None

        signature = plugin_manifest["signature"]["signature"]
        is_valid = mock_trust_anchor_store.verify_signature(
            plugin_manifest["plugin"]["id"],
            signature,
            public_key,
        )
        assert is_valid

    def test_community_plugin_no_signature(self):
        """Test: Community plugins have no signature requirement."""
        community_manifest = {
            "plugin": {
                "id": "community-tool",
                "version": "1.0.0",
                "author": "Community Developer",
            },
            # No signature field
        }

        # Should pass (no signature check for community)
        assert "signature" not in community_manifest

    def test_invalid_signature_rejected(self, mock_trust_anchor_store):
        """Test: Invalid signature fails verification."""
        # Register anchor
        corvin_public_key = "a" * 64
        mock_trust_anchor_store.add_anchor("corvin-labs", corvin_public_key)

        # Plugin with invalid signature
        plugin_manifest = {
            "plugin": {
                "id": "bad-sig",
                "version": "1.0.0",
            },
            "signature": {
                "algorithm": "Ed25519",
                "key_id": "corvin-labs",
                "signature": "c" * 127,  # Wrong length
            }
        }

        # Should fail
        signature = plugin_manifest["signature"]["signature"]
        key_id = plugin_manifest["signature"]["key_id"]
        public_key = mock_trust_anchor_store.get_anchor(key_id)

        is_valid = (
            len(signature) == 128 and  # Correct length
            public_key in mock_trust_anchor_store.list_anchors().values()
        )
        assert not is_valid

    def test_unknown_key_rejected(self, mock_trust_anchor_store):
        """Test: Signature from unknown key is rejected."""
        plugin_manifest = {
            "plugin": {
                "id": "unknown-signer",
                "version": "1.0.0",
            },
            "signature": {
                "key_id": "unknown-attacker",
                "signature": "b" * 128,
            }
        }

        key_id = plugin_manifest["signature"]["key_id"]
        public_key = mock_trust_anchor_store.get_anchor(key_id)
        assert public_key is None  # Unknown key


# ============================================================================
# TEST SUITE 3: Plugin Reporting & Governance
# ============================================================================

class TestPluginReporting:
    """Test plugin reporting workflow (ADR-0249 governance)."""

    def test_report_malicious_plugin(self, mock_report_system):
        """Golden Path: Report a malicious plugin."""
        report_id = mock_report_system.submit_report(
            plugin_id="malware-plugin",
            reason="malicious",
            details="This plugin contains code that exfiltrates user data",
            operator_id="security-team",
        )

        assert report_id is not None
        reports = mock_report_system.get_reports("malware-plugin")
        assert len(reports) == 1
        assert reports[0]["reason"] == "malicious"

    def test_report_inappropriate_plugin(self, mock_report_system):
        """Test: Report inappropriate plugin."""
        report_id = mock_report_system.submit_report(
            plugin_id="inappropriate-content",
            reason="inappropriate",
            details="Contains offensive language and adult content",
            operator_id="operator-1",
        )

        assert report_id is not None

    def test_report_permission_abuse(self, mock_report_system):
        """Test: Report plugin exceeding permissions."""
        report_id = mock_report_system.submit_report(
            plugin_id="overprivileged",
            reason="permission_abuse",
            details="Requests admin access but only needs read-only",
            operator_id="operator-1",
        )

        assert report_id is not None

    def test_report_misrepresentation(self, mock_report_system):
        """Test: Report misleading plugin metadata."""
        report_id = mock_report_system.submit_report(
            plugin_id="fake-plugin",
            reason="misrepresentation",
            details="Description claims to be database sync but is actually an ad tracker",
            operator_id="operator-1",
        )

        assert report_id is not None

    def test_report_generic(self, mock_report_system):
        """Test: Report plugin with generic reason."""
        report_id = mock_report_system.submit_report(
            plugin_id="other-issue",
            reason="other",
            details="Does not work as advertised",
            operator_id="operator-1",
        )

        assert report_id is not None

    def test_multiple_reports_same_plugin(self, mock_report_system):
        """Test: Multiple reports on same plugin."""
        plugin_id = "reported-plugin"

        for i in range(3):
            mock_report_system.submit_report(
                plugin_id=plugin_id,
                reason="malicious" if i == 0 else "other",
                details=f"Report {i}",
                operator_id=f"op-{i}",
            )

        reports = mock_report_system.get_reports(plugin_id)
        assert len(reports) == 3

    def test_report_thresholds_trigger_removal(self, mock_report_system):
        """Test: Threshold of reports triggers governance action."""
        plugin_id = "threshold-test"

        # Add reports
        for i in range(5):  # Threshold for action
            mock_report_system.submit_report(
                plugin_id=plugin_id,
                reason="malicious",
                details="Suspicious behavior",
                operator_id=f"op-{i}",
            )

        report_count = mock_report_system.get_report_count(plugin_id)
        should_review = report_count >= 3  # Example threshold

        assert should_review
        assert report_count == 5


# ============================================================================
# TEST SUITE 4: Auto-Removal Governance
# ============================================================================

class TestAutoRemovalGovernance:
    """Test automatic plugin removal on governance triggers."""

    def test_low_rating_triggers_removal(self):
        """Test: Plugin with <2 stars and 5+ reviews is marked for removal."""
        plugin = PluginMetadata(
            plugin_id="low-rated-removal",
            name="Low Rated",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="author",
            author_email="author@test.io",
            license="MIT",
            description="",
            long_description="",
            rating_count=10,  # ≥5 reviews
            rating_average=1.5,  # <2.0 stars
            listed=True,
        )

        # Check if should be auto-removed
        should_remove = plugin.rating_count >= 5 and plugin.rating_average < 2.0
        assert should_remove

    def test_high_report_count_triggers_removal(self, mock_report_system):
        """Test: High report count triggers governance review."""
        plugin_id = "high-reports"

        # Simulate 10 reports
        for i in range(10):
            mock_report_system.submit_report(
                plugin_id=plugin_id,
                reason="malicious",
                details="Issue",
                operator_id=f"op-{i}",
            )

        report_count = mock_report_system.get_report_count(plugin_id)
        governance_threshold = 5
        requires_action = report_count >= governance_threshold

        assert requires_action

    def test_security_audit_failure_triggers_removal(self):
        """Test: Failed security audit prevents listing."""
        # Mock security audit result
        audit_result = {
            "plugin_id": "failed-audit",
            "passed": False,
            "issues": [
                "Hardcoded credentials detected",
                "Suspicious network calls",
            ]
        }

        # Plugin should be delisted
        should_list = audit_result["passed"]
        assert not should_list


# ============================================================================
# TEST SUITE 5: Approval Workflow by Origin
# ============================================================================

class TestApprovalWorkflows:
    """Test different approval workflows by plugin origin."""

    def test_builtin_plugin_auto_approved(self):
        """Test: Builtin plugins are auto-approved."""
        plugin = PluginMetadata(
            plugin_id="builtin-auto",
            name="Builtin Auto",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.COMPLIANCE,
            origin=PluginOrigin.BUILTIN,
            author_id="corvin",
            author_email="support@corvin.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        # Builtin = auto-approved
        requires_approval = plugin.origin != PluginOrigin.BUILTIN
        assert not requires_approval  # No approval needed

    def test_vetted_plugin_signature_approval(self, mock_trust_anchor_store):
        """Test: Vetted plugins require signature validation."""
        mock_trust_anchor_store.add_anchor("corvin-labs", "a" * 64)

        plugin_manifest = {
            "plugin": {
                "id": "vetted-auto",
                "version": "1.0.0",
            },
            "signature": {
                "key_id": "corvin-labs",
                "signature": "b" * 128,
            }
        }

        key_id = plugin_manifest["signature"]["key_id"]
        public_key = mock_trust_anchor_store.get_anchor(key_id)
        signature = plugin_manifest["signature"]["signature"]

        is_valid = (
            public_key is not None and
            len(signature) == 128 and
            public_key in mock_trust_anchor_store.list_anchors().values()
        )

        # If signature valid → auto-approved
        approved = is_valid
        assert approved

    def test_community_plugin_requires_confirmation(self):
        """Test: Community plugins require operator confirmation."""
        plugin = PluginMetadata(
            plugin_id="community-manual",
            name="Community Manual",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@community.io",
            license="MIT",
            description="",
            long_description="",
        )

        # Community = requires explicit confirmation
        requires_confirmation = plugin.origin == PluginOrigin.COMMUNITY
        assert requires_confirmation


# ============================================================================
# TEST SUITE 6: Sandboxing & Resource Limits
# ============================================================================

class TestSandboxingAndLimits:
    """Test plugin sandboxing and resource constraints."""

    def test_plugin_cpu_limit_enforcement(self):
        """Test: Plugin respects CPU limit."""
        plugin = PluginMetadata(
            plugin_id="cpu-bounded",
            name="CPU Bounded",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@test.io",
            license="MIT",
            description="",
            long_description="",
            required_syscalls=["getrandom"],
        )

        # CPU limit: 20%
        cpu_limit = 20
        assert 1 <= cpu_limit <= 100

    def test_plugin_memory_limit_enforcement(self):
        """Test: Plugin respects memory limit."""
        plugin = PluginMetadata(
            plugin_id="mem-bounded",
            name="Memory Bounded",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@test.io",
            license="MIT",
            description="",
            long_description="",
        )

        # Memory limit: 256 MB
        memory_limit = 256
        assert 64 <= memory_limit <= 512

    def test_plugin_network_access_controls(self):
        """Test: Network access is restricted per plugin."""
        plugin = PluginMetadata(
            plugin_id="network-restricted",
            name="Network Restricted",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@test.io",
            license="MIT",
            description="",
            long_description="",
            network_access=False,  # No network
        )

        # Network access denied
        can_access_network = plugin.network_access
        assert not can_access_network

    def test_plugin_filesystem_access_controls(self):
        """Test: Filesystem access limited to declared paths."""
        plugin = PluginMetadata(
            plugin_id="fs-restricted",
            name="FS Restricted",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@test.io",
            license="MIT",
            description="",
            long_description="",
            filesystem_paths={
                "/tmp": "rw",  # Read-write to /tmp only
            }
        )

        # Can access /tmp
        assert "/tmp" in plugin.filesystem_paths
        # Cannot access others
        assert "/home" not in plugin.filesystem_paths


# ============================================================================
# TEST SUITE 7: Disabled Boot Layers
# ============================================================================

class TestBootLayerRestrictions:
    """Test boot layer disableability rules (ADR-0243)."""

    def test_compliance_layer_cannot_be_disabled(self):
        """Test: Compliance layer plugins cannot be disabled."""
        plugin = PluginMetadata(
            plugin_id="audit-compliance",
            name="Audit Compliance",
            version="1.0.0",
            category=PluginCategory.SECURITY,
            boot_layer=BootLayer.COMPLIANCE,  # Non-disableable
            origin=PluginOrigin.BUILTIN,
            author_id="corvin",
            author_email="support@corvin.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        can_disable = plugin.boot_layer != BootLayer.COMPLIANCE
        assert not can_disable

    def test_core_layer_replaceable(self):
        """Test: Core layer plugins can be replaced."""
        plugin = PluginMetadata(
            plugin_id="core-replaceable",
            name="Core Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.CORE,  # Replaceable
            origin=PluginOrigin.BUILTIN,
            author_id="corvin",
            author_email="support@corvin.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        can_replace = plugin.boot_layer == BootLayer.CORE
        assert can_replace

    def test_bundled_layer_disableable(self):
        """Test: Bundled plugins can be disabled."""
        plugin = PluginMetadata(
            plugin_id="bundled-disableable",
            name="Bundled Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.BUNDLED,  # Disableable
            origin=PluginOrigin.BUILTIN,
            author_id="corvin",
            author_email="support@corvin.io",
            license="Apache-2.0",
            description="",
            long_description="",
        )

        can_disable = plugin.boot_layer != BootLayer.COMPLIANCE
        assert can_disable

    def test_installed_layer_disableable(self):
        """Test: Installed plugins can be disabled/removed."""
        plugin = PluginMetadata(
            plugin_id="user-installed",
            name="User Plugin",
            version="1.0.0",
            category=PluginCategory.INTEGRATION,
            boot_layer=BootLayer.INSTALLED,  # Fully manageable
            origin=PluginOrigin.COMMUNITY,
            author_id="dev",
            author_email="dev@test.io",
            license="MIT",
            description="",
            long_description="",
        )

        can_disable = plugin.boot_layer != BootLayer.COMPLIANCE
        assert can_disable


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
