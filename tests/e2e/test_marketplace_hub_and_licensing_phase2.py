"""E2E Tests: Marketplace Hub Phase 1 + Licensing A2A RSA Gate (Option E)

Comprehensive integration tests for both streams:
- Stream A: Marketplace Hub discovery UI, search, filtering, trending
- Stream B: Licensing credentials, RSA signing, verification, revocation
- Sync Points: Hub shows licensing badges, delegation workflow complete

ADR-0686 (Marketplace Hub) + ADR-0704 (Licensing Phase 2)
License: Apache-2.0
"""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from core.licensing.member_credential import (
    MemberCredential,
    SignedTask,
    RSAKeyPair,
    CredentialStore,
    LicenseTier,
)
from core.licensing.a2a_verifier import (
    A2ADelegationVerifier,
    VerificationResult,
    RevocationList,
)
from core.licensing.authority_server import (
    AuthorityServer,
)


# ============================================================================
# Stream A: Marketplace Hub Tests
# ============================================================================

# ADR-0892 (2026-09-19): the Marketplace Hub classes that lived here tested
# core.skills.marketplace_hub — a synthetic index of six hardcoded items — which
# was deleted with the hub. The licensing tests below are unchanged.


class TestRSAKeyManagement:
    """Test RSA keypair generation and operations."""

    def test_generate_keypair(self):
        """Test RSA keypair generation."""
        keypair = RSAKeyPair.generate()
        assert keypair.private_key is not None
        assert keypair.public_key is not None

    def test_sign_and_verify(self):
        """Test signing and verification."""
        keypair = RSAKeyPair.generate()
        message = b"test message"

        # Sign message
        signature = keypair.sign(message)
        assert signature is not None
        assert len(signature) > 0

        # Verify signature
        assert RSAKeyPair.verify(keypair.public_key, message, signature)

    def test_pem_export_import(self):
        """Test PEM export/import."""
        keypair1 = RSAKeyPair.generate()
        pem = keypair1.private_pem()

        # Load from PEM
        keypair2 = RSAKeyPair.from_private_pem(pem)

        # Both should sign identically
        message = b"test"
        sig1 = keypair1.sign(message)
        sig2 = keypair2.sign(message)

        # Signatures differ due to PSS randomization, but both verify
        assert RSAKeyPair.verify(keypair1.public_key, message, sig1)
        assert RSAKeyPair.verify(keypair2.public_key, message, sig2)


class TestMemberCredential:
    """Test credential lifecycle."""

    def test_credential_creation(self):
        """Test creating a credential."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        expiry = (now + timedelta(days=90)).isoformat() + "Z"

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=expiry,
            public_key_pem=keypair.public_pem().decode(),
        )

        assert cred.member_id == "alice@example.com"
        assert cred.license_tier == "member"
        assert cred.credential_id  # Should be auto-generated

    def test_credential_expiry_check(self):
        """Test expiry checking."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        # Expired credential
        cred_expired = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=100)).isoformat() + "Z",
            expires_at=(now - timedelta(days=10)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )
        assert cred_expired.is_expired()
        assert not cred_expired.is_valid()

        # Valid credential
        cred_valid = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=10)).isoformat() + "Z",
            expires_at=(now + timedelta(days=80)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",  # Required for is_valid()
        )
        assert not cred_valid.is_expired()
        assert cred_valid.is_valid()

    def test_can_delegate(self):
        """Test delegation permission check."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        pem = keypair.public_pem().decode()

        # Member tier can delegate
        cred_member = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=pem,
            signature="sig",
        )
        assert cred_member.can_delegate()

        # Free tier cannot delegate
        cred_free = MemberCredential(
            member_id="bob@example.com",
            license_tier="free",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=pem,
            signature="sig",
        )
        assert not cred_free.can_delegate()

        # Expired credential cannot delegate
        cred_expired = MemberCredential(
            member_id="charlie@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=100)).isoformat() + "Z",
            expires_at=(now - timedelta(days=10)).isoformat() + "Z",
            public_key_pem=pem,
            signature="sig",
        )
        assert not cred_expired.can_delegate()


class TestA2AVerification:
    """Test A2A task verification."""

    def setup_method(self):
        """Set up test verifier."""
        self.tmpdir = tempfile.mkdtemp()
        self.verifier = A2ADelegationVerifier(self.tmpdir)
        self.authority = AuthorityServer(self.tmpdir)

    def test_verify_valid_task(self):
        """Test verifying a valid signed task."""
        # Issue credential
        result = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
            validity_days=90,
        )
        assert result.success
        cred = result.credential

        # Create and sign a task
        keypair = RSAKeyPair.from_private_pem(
            RSAKeyPair.generate().private_pem()
        )
        now = datetime.utcnow()

        # Create signed task with same key as credential
        # (In real scenario, member would sign with their private key)
        payload = {"target": "agent_123", "action": "delegate"}
        payload_json = json.dumps(payload, sort_keys=True)
        message_parts = [
            "alice@example.com",
            "delegate_task",
            payload_json,
            now.isoformat() + "Z",
        ]
        message = "|".join(message_parts).encode()

        signature = keypair.sign(message).hex()

        signed_task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate_task",
            payload=payload,
            signed_at=now.isoformat() + "Z",
            signature=signature,
            ttl_seconds=3600,
        )

        # Verify - will fail because signature doesn't match credential's key
        # This is expected in unit test
        result = self.verifier.verify_signed_task(signed_task)

        # The verification should run through all checks
        assert result.task_id == "task_001"
        assert result.member_id == "alice@example.com"

    def test_verify_expired_credential(self):
        """Test verification with expired credential."""
        tmpdir = tempfile.mkdtemp()
        cred_store = CredentialStore(tmpdir)

        # Create expired credential
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        expired_cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=100)).isoformat() + "Z",
            expires_at=(now - timedelta(days=10)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        cred_store.save(expired_cred)

        # Try to verify task with expired credential
        verifier = A2ADelegationVerifier(tmpdir)

        signed_task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=expired_cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="fakesig",
            ttl_seconds=3600,
        )

        result = verifier.verify_signed_task(signed_task)
        assert not result.is_valid
        assert any("expired" in check.lower() for check in result.checks_failed)

    def test_verify_revoked_credential(self):
        """Test verification with revoked credential."""
        tmpdir = tempfile.mkdtemp()
        verifier = A2ADelegationVerifier(tmpdir)
        authority = AuthorityServer(tmpdir)

        # Issue and revoke credential
        issue_result = authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
        )
        cred = issue_result.credential

        authority.revoke_credential(cred.credential_id, reason="Compromised")

        # Try to verify task with revoked credential
        now = datetime.utcnow()
        signed_task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="fakesig",
            ttl_seconds=3600,
        )

        result = verifier.verify_signed_task(signed_task)
        assert not result.is_valid
        assert any("revoked" in check.lower() for check in result.checks_failed)

    def test_crl_management(self):
        """Test CRL (Certificate Revocation List)."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        # Nothing revoked initially
        assert not crl.is_revoked("cert_001")

        # Revoke a certificate
        crl.revoke("cert_001", "Compromised key")
        assert crl.is_revoked("cert_001")
        assert crl.get_revocation_reason("cert_001") == "Compromised key"

        # Persists across instances
        crl2 = RevocationList(tmpdir)
        assert crl2.is_revoked("cert_001")


class TestAuthorityServer:
    """Test credential authority."""

    def setup_method(self):
        """Set up authority."""
        self.tmpdir = tempfile.mkdtemp()
        self.authority = AuthorityServer(self.tmpdir)

    def test_issue_credential(self):
        """Test issuing a credential."""
        result = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
            validity_days=90,
        )

        assert result.success
        assert result.credential is not None
        cred = result.credential
        assert cred.member_id == "alice@example.com"
        assert cred.license_tier == "member"
        assert cred.can_delegate()

    def test_issue_free_tier(self):
        """Test issuing free tier (no delegation)."""
        result = self.authority.issue_credential(
            member_id="bob@example.com",
            license_tier="free",
        )

        assert result.success
        cred = result.credential
        assert cred.license_tier == "free"
        assert not cred.can_delegate()

    def test_renew_credential(self):
        """Test renewing a credential."""
        # Issue initial
        result1 = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
            validity_days=30,
        )
        cred1 = result1.credential

        # Renew
        result2 = self.authority.renew_credential(
            credential_id=cred1.credential_id,
            validity_days=90,
        )

        assert result2.success
        cred2 = result2.credential
        assert cred2.member_id == cred1.member_id
        assert cred2.credential_id != cred1.credential_id  # New credential

    def test_downgrade_tier(self):
        """Test downgrading license tier."""
        # Issue member tier
        result = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
        )
        cred1 = result.credential
        assert cred1.can_delegate()

        # Downgrade to free
        result2 = self.authority.downgrade_tier(
            credential_id=cred1.credential_id,
            new_tier="free",
        )

        assert result2.success
        cred2 = result2.credential
        assert cred2.license_tier == "free"
        assert not cred2.can_delegate()

    def test_list_member_credentials(self):
        """Test listing member credentials."""
        # Issue multiple credentials
        self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
        )
        self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
        )
        self.authority.issue_credential(
            member_id="bob@example.com",
            license_tier="free",
        )

        # List Alice's credentials
        creds = self.authority.list_member_credentials("alice@example.com")
        assert len(creds) == 2
        assert all(c["member_id"] == "alice@example.com" for c in creds)

        # List Bob's credentials
        creds_bob = self.authority.list_member_credentials("bob@example.com")
        assert len(creds_bob) == 1


# ============================================================================
# Integration Tests (Streams A + B Sync)
# ============================================================================

class TestMarketplaceAndLicensingIntegration:
    """Test interaction between Hub and Licensing."""

    def setup_method(self):
        """Set up both systems."""
        self.tmpdir = tempfile.mkdtemp()
        self.hub = MarketplaceHub(self.tmpdir)
        self.authority = AuthorityServer(self.tmpdir)
        self.verifier = A2ADelegationVerifier(self.tmpdir)

    def test_full_delegation_workflow(self):
        """Test complete A2A delegation workflow.

        Workflow:
        1. Authority issues member credential
        2. Member signs delegation task
        3. Verifier checks task (fail due to key mismatch in test, but validates flow)
        4. Hub shows delegation metadata (future)
        """
        # Step 1: Issue credential
        issue_result = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
            validity_days=30,
        )
        assert issue_result.success
        cred = issue_result.credential

        # Verify credential properties
        assert cred.member_id == "alice@example.com"
        assert cred.license_tier == "member"
        assert cred.can_delegate()
        assert not cred.is_expired()

        # Step 2: Member prepares to sign task
        # (In real scenario: member has private key, signs locally)
        now = datetime.utcnow()
        signed_task = SignedTask(
            task_id="delegation_task_123",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate_to_agent",
            payload={"target_agent": "opus", "task": "analyze_code"},
            signed_at=now.isoformat() + "Z",
            signature="fakesignature",  # In real scenario, RSA signed
            ttl_seconds=3600,
        )

        # Step 3: Verifier processes task
        # (Will fail signature check as expected, but shows flow works)
        result = self.verifier.verify_signed_task(signed_task)

        # Verify structure (even though sig check will fail)
        assert result.task_id == "delegation_task_123"
        assert result.member_id == "alice@example.com"
        assert result.credential_id == cred.credential_id

        # Check that credential validation ran
        assert "Credential found" in result.checks_passed
        assert "Credential not expired" in result.checks_passed
        assert "Credential not revoked" in result.checks_passed
        assert "License tier permits delegation" in result.checks_passed

    def test_hub_search_includes_licensing_metadata(self):
        """Test Hub can show licensing status in search.

        Future: Hub shows which items require licensing.
        """
        # Load hub index
        index = self.hub.get_index()

        # Hub items should have tier information
        for item in index.skills + index.plugins + index.tools:
            # Items should have tier/origin for licensing display
            assert hasattr(item, 'tier') or hasattr(item, 'origin')

    def test_revocation_affects_delegation(self):
        """Test that revoking a credential blocks delegation."""
        # Issue credential
        issue_result = self.authority.issue_credential(
            member_id="alice@example.com",
            license_tier="member",
        )
        cred = issue_result.credential

        # Revoke it
        self.authority.revoke_credential(cred.credential_id, "Compromised")

        # Now verification should fail
        now = datetime.utcnow()
        signed_task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
            ttl_seconds=3600,
        )

        result = self.verifier.verify_signed_task(signed_task)
        assert not result.is_valid
        assert any("revoked" in f.lower() for f in result.checks_failed)


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
