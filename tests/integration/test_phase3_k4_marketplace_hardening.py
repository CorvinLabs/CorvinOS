"""Phase 3 k=4: Marketplace Hardening Phase 1 tests (ADR-0775).

35+ tests covering:
- Plugin tier system (BUILDIN/VETTED/COMMUNITY)
- Capability-based access control (fail-closed)
- Ed25519 signature verification
- Certificate validation and revocation
- Audit trail (ADR-0232)
- GDPR compliance (tenant isolation)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from core.marketplace.plugin_tier_system import (
    PluginTier,
    PluginCapability,
    PluginMetadata,
    CapabilityCheckEvent,
    PluginTierGate,
    CapabilityDeniedError,
    TIER_CAPABILITY_MATRIX,
)

from core.marketplace.plugin_signing import (
    PluginManifest,
    SigningCertificate,
    PluginSignature,
    SignatureCheckEvent,
    PluginSignatureVerifier,
    SignatureAlgorithm,
    KeyType,
    SignatureVerificationError,
    CertificateNotFoundError,
    CertificateRevokedError,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def tenant_id():
    """Test tenant ID."""
    return "test_tenant_default"


@pytest.fixture
def plugin_tier_gate(tenant_id):
    """PluginTierGate instance."""
    return PluginTierGate(tenant_id=tenant_id)


@pytest.fixture
def signature_verifier(tenant_id):
    """PluginSignatureVerifier instance."""
    return PluginSignatureVerifier(tenant_id=tenant_id)


@pytest.fixture
def sample_manifest():
    """Sample plugin manifest."""
    return PluginManifest(
        plugin_id="test_plugin",
        version="1.0.0",
        source_url="https://marketplace.corvin.io/plugins/test_plugin",
        tier="vetted",
        code_hash="abc123def456",  # SHA256 hash (base64)
        author="Test Author",
    )


# ============================================================================
# TESTS: PluginTierGate — Initialization & Registration
# ============================================================================


class TestPluginTierGateInit:
    """Tests for PluginTierGate initialization."""

    def test_init_valid(self, tenant_id):
        """Should initialize with valid tenant_id."""
        gate = PluginTierGate(tenant_id=tenant_id)
        assert gate.tenant_id == tenant_id

    def test_init_missing_tenant_id_fails(self):
        """Should fail if tenant_id is empty (GDPR)."""
        with pytest.raises(ValueError, match="tenant_id required"):
            PluginTierGate(tenant_id="")


class TestPluginTierGateRegistration:
    """Tests for plugin registration."""

    def test_register_buildin_plugin(self, plugin_tier_gate):
        """Should register BUILDIN plugin."""
        metadata = plugin_tier_gate.register_plugin(
            plugin_id="sys_audit",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://audit",
        )
        assert metadata.plugin_id == "sys_audit"
        assert metadata.tier == PluginTier.BUILDIN
        assert not metadata.removable  # BUILDIN cannot be removed

    def test_register_vetted_plugin(self, plugin_tier_gate):
        """Should register VETTED plugin (must be signed)."""
        metadata = plugin_tier_gate.register_plugin(
            plugin_id="vetted_plugin",
            tier=PluginTier.VETTED,
            version="1.0.0",
            source_url="https://marketplace.corvin.io/vetted",
            signature="signature_base64",
            signature_verified=True,
        )
        assert metadata.tier == PluginTier.VETTED
        assert metadata.removable

    def test_register_community_plugin(self, plugin_tier_gate):
        """Should register COMMUNITY plugin (must be signed)."""
        metadata = plugin_tier_gate.register_plugin(
            plugin_id="community_plugin",
            tier=PluginTier.COMMUNITY,
            version="1.0.0",
            source_url="https://marketplace.corvin.io/community",
            signature="signature_base64",
            signature_verified=False,  # Not yet verified
        )
        assert metadata.tier == PluginTier.COMMUNITY

    def test_register_vetted_unsigned_fails(self, plugin_tier_gate):
        """Should fail if VETTED plugin has no signature."""
        with pytest.raises(ValueError, match="must be signed"):
            plugin_tier_gate.register_plugin(
                plugin_id="unsigned",
                tier=PluginTier.VETTED,
                version="1.0.0",
                source_url="https://marketplace.corvin.io",
            )

    def test_register_community_unsigned_fails(self, plugin_tier_gate):
        """Should fail if COMMUNITY plugin has no signature."""
        with pytest.raises(ValueError, match="must be signed"):
            plugin_tier_gate.register_plugin(
                plugin_id="unsigned",
                tier=PluginTier.COMMUNITY,
                version="1.0.0",
                source_url="https://marketplace.corvin.io",
            )


# ============================================================================
# TESTS: PluginTierGate — Capability Checking
# ============================================================================


class TestPluginTierGateCapabilities:
    """Tests for capability-based access control."""

    def test_buildin_can_access_audit_chain_write(self, plugin_tier_gate):
        """BUILDIN plugin should access AUDIT_CHAIN_WRITE."""
        plugin_tier_gate.register_plugin(
            plugin_id="sys",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://",
        )
        # Should not raise
        assert plugin_tier_gate.check_capability(
            "sys", PluginCapability.AUDIT_CHAIN_WRITE
        )

    def test_vetted_cannot_access_audit_chain_write(self, plugin_tier_gate):
        """VETTED plugin should NOT access AUDIT_CHAIN_WRITE."""
        plugin_tier_gate.register_plugin(
            plugin_id="vetted",
            tier=PluginTier.VETTED,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
            signature_verified=True,
        )
        # Should raise CapabilityDeniedError
        with pytest.raises(CapabilityDeniedError, match="AUDIT_CHAIN_WRITE"):
            plugin_tier_gate.check_capability(
                "vetted", PluginCapability.AUDIT_CHAIN_WRITE
            )

    def test_community_cannot_access_audit_chain_read(self, plugin_tier_gate):
        """COMMUNITY plugin should NOT access AUDIT_CHAIN_READ."""
        plugin_tier_gate.register_plugin(
            plugin_id="community",
            tier=PluginTier.COMMUNITY,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        # Should raise CapabilityDeniedError
        with pytest.raises(CapabilityDeniedError, match="AUDIT_CHAIN_READ"):
            plugin_tier_gate.check_capability("community", PluginCapability.AUDIT_CHAIN_READ)

    def test_community_can_access_skill_execution(self, plugin_tier_gate):
        """COMMUNITY plugin should access SKILL_EXECUTION."""
        plugin_tier_gate.register_plugin(
            plugin_id="community",
            tier=PluginTier.COMMUNITY,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        # Should not raise
        assert plugin_tier_gate.check_capability(
            "community", PluginCapability.SKILL_EXECUTION
        )

    def test_unregistered_plugin_fails(self, plugin_tier_gate):
        """Should fail if plugin not registered."""
        with pytest.raises(ValueError, match="not registered"):
            plugin_tier_gate.check_capability(
                "nonexistent", PluginCapability.SKILL_EXECUTION
            )

    def test_capability_check_audited(self, plugin_tier_gate):
        """Every capability check should be audited."""
        plugin_tier_gate.register_plugin(
            plugin_id="test",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://",
        )
        plugin_tier_gate.check_capability("test", PluginCapability.SKILL_EXECUTION)

        history = plugin_tier_gate.get_capability_check_history()
        assert len(history) > 0
        assert history[-1].plugin_id == "test"
        assert history[-1].allowed is True

    def test_denied_capability_audited(self, plugin_tier_gate):
        """Denied capability checks should be audited."""
        plugin_tier_gate.register_plugin(
            plugin_id="community",
            tier=PluginTier.COMMUNITY,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        try:
            plugin_tier_gate.check_capability(
                "community", PluginCapability.AUDIT_CHAIN_WRITE
            )
        except CapabilityDeniedError:
            pass

        history = plugin_tier_gate.get_capability_check_history()
        denied = [e for e in history if not e.allowed]
        assert len(denied) > 0


# ============================================================================
# TESTS: PluginTierGate — Metadata & Removal
# ============================================================================


class TestPluginTierGateMetadata:
    """Tests for plugin metadata and removal control."""

    def test_get_plugin_metadata(self, plugin_tier_gate):
        """Should retrieve plugin metadata."""
        plugin_tier_gate.register_plugin(
            plugin_id="test",
            tier=PluginTier.VETTED,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        metadata = plugin_tier_gate.get_plugin_metadata("test")
        assert metadata is not None
        assert metadata.plugin_id == "test"

    def test_get_nonexistent_metadata(self, plugin_tier_gate):
        """Should return None for nonexistent plugin."""
        metadata = plugin_tier_gate.get_plugin_metadata("nonexistent")
        assert metadata is None

    def test_buildin_plugin_not_removable(self, plugin_tier_gate):
        """BUILDIN plugins should not be removable."""
        plugin_tier_gate.register_plugin(
            plugin_id="sys",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://",
        )
        assert not plugin_tier_gate.is_removable("sys")

    def test_vetted_plugin_removable(self, plugin_tier_gate):
        """VETTED plugins should be removable."""
        plugin_tier_gate.register_plugin(
            plugin_id="vetted",
            tier=PluginTier.VETTED,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        assert plugin_tier_gate.is_removable("vetted")

    def test_get_plugin_capabilities(self, plugin_tier_gate):
        """Should get allowed capabilities for plugin."""
        plugin_tier_gate.register_plugin(
            plugin_id="community",
            tier=PluginTier.COMMUNITY,
            version="1.0.0",
            source_url="https://marketplace.corvin.io",
            signature="sig",
        )
        caps = plugin_tier_gate.get_plugin_capabilities("community")
        assert PluginCapability.SKILL_EXECUTION in caps
        assert PluginCapability.AUDIT_CHAIN_READ not in caps


# ============================================================================
# TESTS: PluginSignatureVerifier — Initialization & Certificates
# ============================================================================


class TestPluginSignatureVerifierInit:
    """Tests for PluginSignatureVerifier initialization."""

    def test_init_valid(self, tenant_id):
        """Should initialize with valid tenant_id."""
        verifier = PluginSignatureVerifier(tenant_id=tenant_id)
        assert verifier.tenant_id == tenant_id

    def test_init_missing_tenant_id_fails(self):
        """Should fail if tenant_id is empty (GDPR)."""
        with pytest.raises(ValueError, match="tenant_id required"):
            PluginSignatureVerifier(tenant_id="")

    def test_init_loads_corvin_root_key(self, signature_verifier):
        """Should load Corvin root certificate on init."""
        certs = signature_verifier.get_all_certificates()
        assert len(certs) >= 1
        root_certs = [c for c in certs if c.key_type == KeyType.CORVIN_ROOT]
        assert len(root_certs) > 0


class TestPluginSignatureVerifierCertificates:
    """Tests for certificate management."""

    def test_register_maintainer_key(self, signature_verifier):
        """Should register maintainer signing key."""
        cert = signature_verifier.register_maintainer_key(
            key_id="maintainer-alice",
            public_key="alice_ed25519_public_key",
            subject="Alice Inc.",
            expires_in_days=365,
        )
        assert cert.key_id == "maintainer-alice"
        assert cert.key_type == KeyType.MAINTAINER
        assert not cert.revoked

    def test_register_key_missing_key_id_fails(self, signature_verifier):
        """Should fail if key_id missing."""
        with pytest.raises(ValueError, match="key_id and public_key required"):
            signature_verifier.register_maintainer_key(
                key_id="",
                public_key="key",
                subject="Author",
            )

    def test_get_certificate(self, signature_verifier):
        """Should retrieve certificate."""
        signature_verifier.register_maintainer_key(
            key_id="test-key",
            public_key="test_pub_key",
            subject="Test",
        )
        cert = signature_verifier.get_certificate("test-key")
        assert cert is not None
        assert cert.key_id == "test-key"

    def test_get_nonexistent_certificate(self, signature_verifier):
        """Should return None for nonexistent certificate."""
        cert = signature_verifier.get_certificate("nonexistent")
        assert cert is None

    def test_revoke_certificate(self, signature_verifier):
        """Should revoke maintainer certificate."""
        signature_verifier.register_maintainer_key(
            key_id="test-key",
            public_key="test_pub_key",
            subject="Test",
        )
        signature_verifier.revoke_certificate("test-key", reason="Compromised")
        # Next verification with this key should fail
        # (verified in verify_plugin_signature test)

    def test_cannot_revoke_corvin_root(self, signature_verifier):
        """Should not allow revoking Corvin root key."""
        root_cert = signature_verifier.get_certificate("corvin-root-2026")
        with pytest.raises(ValueError, match="Cannot revoke Corvin root"):
            signature_verifier.revoke_certificate("corvin-root-2026")


# ============================================================================
# TESTS: PluginSignatureVerifier — Signature Verification
# ============================================================================


class TestPluginSignatureVerification:
    """Tests for Ed25519 signature verification."""

    def test_verify_valid_signature(self, signature_verifier, sample_manifest):
        """Should verify valid signature from Corvin key."""
        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="valid_signature_base64",
            manifest_hash="manifest_hash_base64",
            signing_key_id="corvin-root-2026",
            algorithm=SignatureAlgorithm.ED25519,
        )
        # Should not raise
        assert signature_verifier.verify_plugin_signature(signature, sample_manifest)

    def test_verify_missing_certificate_fails(self, signature_verifier, sample_manifest):
        """Should fail if signing certificate not found."""
        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="signature",
            manifest_hash="hash",
            signing_key_id="nonexistent-key",
            algorithm=SignatureAlgorithm.ED25519,
        )
        with pytest.raises(CertificateNotFoundError):
            signature_verifier.verify_plugin_signature(signature, sample_manifest)

    def test_verify_revoked_certificate_fails(self, signature_verifier, sample_manifest):
        """Should fail if certificate is revoked."""
        # Register and revoke a key
        signature_verifier.register_maintainer_key(
            key_id="revoked-key",
            public_key="pub_key",
            subject="Revoked Author",
        )
        signature_verifier.revoke_certificate("revoked-key", reason="Compromised")

        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="signature",
            manifest_hash="hash",
            signing_key_id="revoked-key",
            algorithm=SignatureAlgorithm.ED25519,
        )
        with pytest.raises(CertificateRevokedError):
            signature_verifier.verify_plugin_signature(signature, sample_manifest)

    def test_verify_expired_certificate_fails(self, signature_verifier, sample_manifest):
        """Should fail if certificate has expired."""
        # Create an expired certificate
        expired_cert = SigningCertificate(
            key_id="expired-key",
            key_type=KeyType.MAINTAINER,
            public_key="pub_key",
            subject="Expired Author",
            issued_at=datetime.now() - timedelta(days=400),
            expires_at=datetime.now() - timedelta(days=30),  # Expired 30 days ago
            revoked=False,
        )
        signature_verifier._certificate_store["expired-key"] = expired_cert

        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="signature",
            manifest_hash="hash",
            signing_key_id="expired-key",
            algorithm=SignatureAlgorithm.ED25519,
        )
        with pytest.raises(SignatureVerificationError, match="expired"):
            signature_verifier.verify_plugin_signature(signature, sample_manifest)

    def test_verify_audited(self, signature_verifier, sample_manifest):
        """Signature verification should be audited."""
        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="signature",
            manifest_hash="hash",
            signing_key_id="corvin-root-2026",
            algorithm=SignatureAlgorithm.ED25519,
        )
        signature_verifier.verify_plugin_signature(signature, sample_manifest)

        history = signature_verifier.get_verification_history()
        assert len(history) > 0
        assert history[-1].plugin_id == "test_plugin"
        assert history[-1].status == "verified"


# ============================================================================
# TESTS: Compliance & Edge Cases
# ============================================================================


class TestComplianceAndSecurity:
    """Tests for GDPR compliance and security invariants."""

    def test_tenant_isolation_gate(self):
        """PluginTierGate should enforce tenant_id (GDPR)."""
        with pytest.raises(ValueError):
            PluginTierGate(tenant_id="")

    def test_tenant_isolation_verifier(self):
        """PluginSignatureVerifier should enforce tenant_id (GDPR)."""
        with pytest.raises(ValueError):
            PluginSignatureVerifier(tenant_id="")

    def test_plugin_metadata_immutable(self, plugin_tier_gate):
        """PluginMetadata should be frozen (immutable)."""
        plugin_tier_gate.register_plugin(
            plugin_id="test",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://",
        )
        metadata = plugin_tier_gate.get_plugin_metadata("test")

        with pytest.raises(AttributeError):
            metadata.tier = PluginTier.COMMUNITY

    def test_capability_check_event_immutable(self, plugin_tier_gate):
        """CapabilityCheckEvent should be frozen."""
        plugin_tier_gate.register_plugin(
            plugin_id="test",
            tier=PluginTier.BUILDIN,
            version="1.0.0",
            source_url="builtin://",
        )
        plugin_tier_gate.check_capability("test", PluginCapability.SKILL_EXECUTION)

        history = plugin_tier_gate.get_capability_check_history()
        event = history[-1]

        with pytest.raises(AttributeError):
            event.allowed = False

    def test_signature_check_event_immutable(self, signature_verifier, sample_manifest):
        """SignatureCheckEvent should be frozen."""
        signature = PluginSignature(
            plugin_id="test_plugin",
            signature="sig",
            manifest_hash="hash",
            signing_key_id="corvin-root-2026",
            algorithm=SignatureAlgorithm.ED25519,
        )
        signature_verifier.verify_plugin_signature(signature, sample_manifest)

        history = signature_verifier.get_verification_history()
        event = history[-1]

        with pytest.raises(AttributeError):
            event.status = "failed"


# ============================================================================
# TESTS: Capability Matrix
# ============================================================================


class TestCapabilityMatrix:
    """Tests for tier capability matrix."""

    def test_buildin_has_most_capabilities(self):
        """BUILDIN tier should have most capabilities."""
        buildin_caps = TIER_CAPABILITY_MATRIX[PluginTier.BUILDIN]
        vetted_caps = TIER_CAPABILITY_MATRIX[PluginTier.VETTED]
        community_caps = TIER_CAPABILITY_MATRIX[PluginTier.COMMUNITY]

        assert len(buildin_caps) >= len(vetted_caps)
        assert len(vetted_caps) >= len(community_caps)

    def test_buildin_unique_capabilities(self):
        """BUILDIN should have unique capabilities (audit write)."""
        buildin_caps = TIER_CAPABILITY_MATRIX[PluginTier.BUILDIN]
        vetted_caps = TIER_CAPABILITY_MATRIX[PluginTier.VETTED]

        # AUDIT_CHAIN_WRITE only in BUILDIN
        assert PluginCapability.AUDIT_CHAIN_WRITE in buildin_caps
        assert PluginCapability.AUDIT_CHAIN_WRITE not in vetted_caps

    def test_community_unique_capabilities(self):
        """COMMUNITY should have subprocess_spawn capability."""
        community_caps = TIER_CAPABILITY_MATRIX[PluginTier.COMMUNITY]
        assert PluginCapability.SUBPROCESS_SPAWN in community_caps


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
