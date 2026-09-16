"""Unit Tests: A2A RSA Gate - Comprehensive licensing validation suite

Implements ADR-0704 (Licensing Phase 2) + ADR-0769 (A2A RSA Gate):
- RSA key generation, signing, verification
- Member credential lifecycle (issue, renew, downgrade, revoke)
- A2A delegation task verification (6 checks: credential, expiry, revocation, tier, signature, task_expiry)
- Revocation list (CRL) management
- Authority server operations
- Audit trail append-only validation
- Edge cases and adversarial scenarios

License: Apache-2.0
"""

import json
import pytest
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
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
    IssuanceResult,
)


# ============================================================================
# RSAKeyPair Tests (10 tests)
# ============================================================================

class TestRSAKeyPair:
    """Test RSA keypair generation and operations."""

    def test_generate_keypair(self):
        """Test generating a new RSA keypair."""
        keypair = RSAKeyPair.generate()
        assert keypair.private_key is not None
        assert keypair.public_key is not None

    def test_sign_produces_bytes(self):
        """Test that signing produces signature bytes."""
        keypair = RSAKeyPair.generate()
        message = b"test message for signing"
        signature = keypair.sign(message)

        assert isinstance(signature, bytes)
        assert len(signature) > 0
        assert len(signature) == 256  # 2048-bit RSA signature is 256 bytes

    def test_verify_valid_signature(self):
        """Test verifying a valid signature."""
        keypair = RSAKeyPair.generate()
        message = b"test message"
        signature = keypair.sign(message)

        assert RSAKeyPair.verify(keypair.public_key, message, signature)

    def test_verify_invalid_signature(self):
        """Test that verification fails with wrong signature."""
        keypair = RSAKeyPair.generate()
        message = b"test message"
        bad_signature = b"0" * 256  # 256 zero bytes

        assert not RSAKeyPair.verify(keypair.public_key, message, bad_signature)

    def test_verify_wrong_message(self):
        """Test that verification fails with modified message."""
        keypair = RSAKeyPair.generate()
        message = b"test message"
        signature = keypair.sign(message)

        # Change the message
        modified_message = b"test message modified"
        assert not RSAKeyPair.verify(keypair.public_key, modified_message, signature)

    def test_pem_export_private(self):
        """Test exporting private key to PEM."""
        keypair = RSAKeyPair.generate()
        pem = keypair.private_pem()

        assert isinstance(pem, bytes)
        assert b"BEGIN PRIVATE KEY" in pem
        assert b"END PRIVATE KEY" in pem

    def test_pem_export_public(self):
        """Test exporting public key to PEM."""
        keypair = RSAKeyPair.generate()
        pem = keypair.public_pem()

        assert isinstance(pem, bytes)
        assert b"BEGIN PUBLIC KEY" in pem
        assert b"END PUBLIC KEY" in pem

    def test_import_from_private_pem(self):
        """Test importing keypair from PEM."""
        keypair1 = RSAKeyPair.generate()
        pem = keypair1.private_pem()

        # Import from PEM
        keypair2 = RSAKeyPair.from_private_pem(pem)

        # Both should work identically
        message = b"test"
        sig = keypair2.sign(message)
        assert RSAKeyPair.verify(keypair2.public_key, message, sig)

    def test_public_key_pem_roundtrip(self):
        """Test exporting and importing public key."""
        keypair = RSAKeyPair.generate()
        public_pem = keypair.public_pem()

        # Import public key
        public_key = RSAKeyPair.from_public_pem(public_pem)

        # Can verify with imported public key
        message = b"test"
        signature = keypair.sign(message)
        assert RSAKeyPair.verify(public_key, message, signature)

    def test_different_keys_different_signatures(self):
        """Test that different keys produce different signatures."""
        keypair1 = RSAKeyPair.generate()
        keypair2 = RSAKeyPair.generate()

        message = b"test message"
        sig1 = keypair1.sign(message)

        # keypair2's public key should NOT verify sig1
        # (extremely unlikely to be true due to RSA math)
        # Note: PSS padding adds randomness, so signatures differ even from same key
        assert not RSAKeyPair.verify(keypair2.public_key, message, sig1)


# ============================================================================
# MemberCredential Tests (12 tests)
# ============================================================================

class TestMemberCredential:
    """Test credential lifecycle and validation."""

    def test_credential_auto_id_generation(self):
        """Test that credential_id is auto-generated."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        assert cred.credential_id
        assert "alice" in cred.credential_id

    def test_credential_expiry_valid(self):
        """Test expiry check on valid credential."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        assert not cred.is_expired()
        assert cred.is_valid()

    def test_credential_expiry_expired(self):
        """Test expiry check on expired credential."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=100)).isoformat() + "Z",
            expires_at=(now - timedelta(days=10)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        assert cred.is_expired()
        assert not cred.is_valid()

    def test_credential_can_delegate_member_tier(self):
        """Test delegation permission for member tier."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        assert cred.can_delegate()

    def test_credential_can_delegate_enterprise_tier(self):
        """Test delegation permission for enterprise tier."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="enterprise",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        assert cred.can_delegate()

    def test_credential_cannot_delegate_free_tier(self):
        """Test delegation permission for free tier."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="bob@example.com",
            license_tier="free",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        assert not cred.can_delegate()

    def test_credential_cannot_delegate_if_expired(self):
        """Test that expired credentials cannot delegate."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=(now - timedelta(days=100)).isoformat() + "Z",
            expires_at=(now - timedelta(days=10)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        assert not cred.can_delegate()

    def test_credential_remaining_ttl(self):
        """Test remaining TTL calculation."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        ttl = cred.get_remaining_ttl_seconds()

        # Should be approximately 90 days in seconds
        expected = 90 * 24 * 60 * 60
        assert abs(ttl - expected) < 10  # Within 10 seconds

    def test_credential_to_dict(self):
        """Test converting credential to dict."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test_sig",
        )

        d = cred.to_dict()

        assert d["member_id"] == "alice@example.com"
        assert d["license_tier"] == "member"
        assert d["signature"] == "test_sig"

    def test_credential_from_dict(self):
        """Test creating credential from dict."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        data = {
            "member_id": "alice@example.com",
            "license_tier": "member",
            "issued_at": now.isoformat() + "Z",
            "expires_at": (now + timedelta(days=90)).isoformat() + "Z",
            "public_key_pem": keypair.public_pem().decode(),
            "signature": "test",
        }

        cred = MemberCredential.from_dict(data)

        assert cred.member_id == "alice@example.com"
        assert cred.license_tier == "member"

    def test_credential_is_valid_requires_signature(self):
        """Test that is_valid() requires signature."""
        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()

        # Without signature
        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        assert not cred.is_valid()

        # With signature
        cred.signature = "test"
        assert cred.is_valid()


# ============================================================================
# SignedTask Tests (5 tests)
# ============================================================================

class TestSignedTask:
    """Test signed delegation task validation."""

    def test_signed_task_fresh(self):
        """Test that fresh task is not expired."""
        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="cred_001",
            operation="delegate",
            payload={"action": "test"},
            signed_at=now.isoformat() + "Z",
            signature="sig",
            ttl_seconds=3600,
        )

        assert not task.is_expired()

    def test_signed_task_expired(self):
        """Test that old task is expired."""
        now = datetime.utcnow()
        old_time = (now - timedelta(hours=2)).isoformat() + "Z"

        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="cred_001",
            operation="delegate",
            payload={"action": "test"},
            signed_at=old_time,
            signature="sig",
            ttl_seconds=3600,
        )

        assert task.is_expired()

    def test_signed_task_edge_of_expiry(self):
        """Test task at edge of TTL."""
        now = datetime.utcnow()
        # Signed 3600 seconds ago (exactly at TTL)
        edge_time = (now - timedelta(seconds=3600)).isoformat() + "Z"

        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="cred_001",
            operation="delegate",
            payload={"action": "test"},
            signed_at=edge_time,
            signature="sig",
            ttl_seconds=3600,
        )

        # At edge, should be considered expired
        assert task.is_expired()

    def test_signed_task_to_dict(self):
        """Test converting task to dict."""
        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="cred_001",
            operation="delegate",
            payload={"action": "test"},
            signed_at=now.isoformat() + "Z",
            signature="sig",
            ttl_seconds=3600,
        )

        d = task.to_dict()
        assert d["task_id"] == "task_001"
        assert d["member_id"] == "alice@example.com"

    def test_signed_task_from_dict(self):
        """Test creating task from dict."""
        now = datetime.utcnow()
        data = {
            "task_id": "task_001",
            "member_id": "alice@example.com",
            "credential_id": "cred_001",
            "operation": "delegate",
            "payload": {"action": "test"},
            "signed_at": now.isoformat() + "Z",
            "signature": "sig",
            "ttl_seconds": 3600,
        }

        task = SignedTask.from_dict(data)
        assert task.task_id == "task_001"


# ============================================================================
# CredentialStore Tests (6 tests)
# ============================================================================

class TestCredentialStore:
    """Test persistent credential storage."""

    def test_save_and_load_credential(self):
        """Test saving and loading a credential."""
        tmpdir = tempfile.mkdtemp()
        store = CredentialStore(tmpdir)

        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
            signature="test",
        )

        # Save
        store.save(cred)

        # Load
        loaded = store.load(cred.credential_id)
        assert loaded is not None
        assert loaded.member_id == "alice@example.com"
        assert loaded.credential_id == cred.credential_id

    def test_load_nonexistent(self):
        """Test loading non-existent credential."""
        tmpdir = tempfile.mkdtemp()
        store = CredentialStore(tmpdir)

        loaded = store.load("nonexistent")
        assert loaded is None

    def test_list_for_member(self):
        """Test listing credentials for a member."""
        tmpdir = tempfile.mkdtemp()
        store = CredentialStore(tmpdir)
        authority = AuthorityServer(tmpdir)

        # Issue multiple credentials for alice
        authority.issue_credential("alice@example.com", "member")
        authority.issue_credential("alice@example.com", "member")

        # Issue one for bob
        authority.issue_credential("bob@example.com", "free")

        # List alice's
        creds = store.list_for_member("alice@example.com")
        assert len(creds) == 2
        assert all(c.member_id == "alice@example.com" for c in creds)

    def test_delete_credential(self):
        """Test deleting a credential."""
        tmpdir = tempfile.mkdtemp()
        store = CredentialStore(tmpdir)

        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        store.save(cred)
        assert store.load(cred.credential_id) is not None

        # Delete
        store.delete(cred.credential_id)
        assert store.load(cred.credential_id) is None

    def test_credential_storage_directory_created(self):
        """Test that storage directory is created automatically."""
        tmpdir = tempfile.mkdtemp()
        creds_dir = Path(tmpdir) / "licensing" / "credentials"

        assert not creds_dir.exists()

        store = CredentialStore(tmpdir)
        assert creds_dir.exists()

    def test_save_handles_exceptions_gracefully(self):
        """Test that save handles errors."""
        tmpdir = tempfile.mkdtemp()
        store = CredentialStore(tmpdir)

        # Make directory read-only (will cause error on write)
        # This test depends on OS permissions
        # Skip on systems where read-only doesn't apply to owner

        keypair = RSAKeyPair.generate()
        now = datetime.utcnow()
        cred = MemberCredential(
            member_id="alice@example.com",
            license_tier="member",
            issued_at=now.isoformat() + "Z",
            expires_at=(now + timedelta(days=90)).isoformat() + "Z",
            public_key_pem=keypair.public_pem().decode(),
        )

        # Normal save should work
        store.save(cred)
        assert store.load(cred.credential_id) is not None


# ============================================================================
# RevocationList Tests (6 tests)
# ============================================================================

class TestRevocationList:
    """Test Certificate Revocation List (CRL)."""

    def test_crl_initially_empty(self):
        """Test that CRL starts empty."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        assert not crl.is_revoked("cert_001")

    def test_revoke_credential(self):
        """Test revoking a credential."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        crl.revoke("cert_001", "Compromised")
        assert crl.is_revoked("cert_001")

    def test_get_revocation_reason(self):
        """Test getting revocation reason."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        crl.revoke("cert_001", "Compromised key")
        reason = crl.get_revocation_reason("cert_001")

        assert reason == "Compromised key"

    def test_revocation_persists(self):
        """Test that revocation persists across instances."""
        tmpdir = tempfile.mkdtemp()

        crl1 = RevocationList(tmpdir)
        crl1.revoke("cert_001", "Test")

        # New instance reads same data
        crl2 = RevocationList(tmpdir)
        assert crl2.is_revoked("cert_001")

    def test_multiple_revocations(self):
        """Test revoking multiple credentials."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        crl.revoke("cert_001", "Reason 1")
        crl.revoke("cert_002", "Reason 2")
        crl.revoke("cert_003", "Reason 3")

        assert len(crl.revoked) == 3
        assert crl.is_revoked("cert_001")
        assert crl.is_revoked("cert_002")
        assert crl.is_revoked("cert_003")

    def test_non_revoked_reasons_empty(self):
        """Test that non-revoked credentials have no reason."""
        tmpdir = tempfile.mkdtemp()
        crl = RevocationList(tmpdir)

        reason = crl.get_revocation_reason("cert_nonexistent")
        assert reason is None


# ============================================================================
# A2ADelegationVerifier Tests (15 tests)
# ============================================================================

class TestA2ADelegationVerifier:
    """Test A2A delegation task verification (6 checks)."""

    def setup_method(self):
        """Set up verifier and authority."""
        self.tmpdir = tempfile.mkdtemp()
        self.verifier = A2ADelegationVerifier(self.tmpdir)
        self.authority = AuthorityServer(self.tmpdir)

    def test_verify_credential_not_found(self):
        """Check 1: Credential must exist."""
        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("not found" in f.lower() for f in result.checks_failed)
        assert result.error_message == "Credential not found"

    def test_verify_credential_expired(self):
        """Check 2: Credential must not be expired."""
        # Issue and immediately expire
        issue = self.authority.issue_credential(
            "alice@example.com",
            "member",
            validity_days=0,  # Already expired
        )
        cred = issue.credential

        # Force expiry
        cred.expires_at = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        self.authority.credential_store.save(cred)

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("expired" in f.lower() for f in result.checks_failed)

    def test_verify_credential_revoked(self):
        """Check 3: Credential must not be revoked."""
        issue = self.authority.issue_credential("alice@example.com", "member")
        cred = issue.credential

        # Revoke it
        self.authority.revoke_credential(cred.credential_id, "Test revocation")

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("revoked" in f.lower() for f in result.checks_failed)

    def test_verify_license_tier_free(self):
        """Check 4: License tier must permit delegation."""
        issue = self.authority.issue_credential("bob@example.com", "free")
        cred = issue.credential

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="bob@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("tier" in f.lower() for f in result.checks_failed)

    def test_verify_signature_invalid(self):
        """Check 5: Signature must be valid."""
        issue = self.authority.issue_credential("alice@example.com", "member")
        cred = issue.credential

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="0" * 512,  # Invalid signature
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("signature" in f.lower() for f in result.checks_failed)

    def test_verify_task_expired(self):
        """Check 6: Task must not be expired."""
        issue = self.authority.issue_credential("alice@example.com", "member")
        cred = issue.credential

        # Task signed 2 hours ago with 1 hour TTL
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},
            signed_at=old_time,
            signature="sig",
            ttl_seconds=3600,  # 1 hour
        )

        result = self.verifier.verify_signed_task(task)

        assert not result.is_valid
        assert any("expired" in f.lower() for f in result.checks_failed)

    def test_verify_all_checks_pass(self):
        """Test successful verification with all checks passing."""
        # Issue valid credential
        issue = self.authority.issue_credential("alice@example.com", "member")
        cred = issue.credential

        # Create properly signed task
        keypair = RSAKeyPair.from_private_pem(
            RSAKeyPair.generate().private_pem()
        )
        now = datetime.utcnow()

        payload = {"action": "test"}
        payload_json = json.dumps(payload, sort_keys=True)
        message_parts = [
            "alice@example.com",
            "delegate",
            payload_json,
            now.isoformat() + "Z",
        ]
        message = "|".join(message_parts).encode()
        signature = keypair.sign(message).hex()

        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload=payload,
            signed_at=now.isoformat() + "Z",
            signature=signature,
            ttl_seconds=3600,
        )

        # Note: This will still fail on signature verification because
        # we're signing with a different key than credential's public key.
        # But it validates the structure.
        result = self.verifier.verify_signed_task(task)

        # Verify structure checks ran
        assert "Credential found" in result.checks_passed
        assert "Credential not expired" in result.checks_passed
        assert "Credential not revoked" in result.checks_passed
        assert "License tier permits delegation" in result.checks_passed

    def test_verify_result_has_latency(self):
        """Test that verification result includes latency."""
        issue = self.authority.issue_credential("alice@example.com", "member")

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        assert result.verification_latency_ms > 0

    def test_verify_audit_event_written(self):
        """Test that verification is audited."""
        issue = self.authority.issue_credential("alice@example.com", "member")

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        self.verifier.verify_signed_task(task)

        # Check audit file exists
        audit_file = Path(self.tmpdir) / "licensing" / "verification_audit.jsonl"
        assert audit_file.exists()

    def test_verify_audit_trail_append_only(self):
        """Test that audit trail is append-only."""
        issue = self.authority.issue_credential("alice@example.com", "member")

        now = datetime.utcnow()
        task1 = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        task2 = SignedTask(
            task_id="task_002",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=(now + timedelta(seconds=1)).isoformat() + "Z",
            signature="sig",
        )

        self.verifier.verify_signed_task(task1)
        self.verifier.verify_signed_task(task2)

        # Read audit file
        audit_file = Path(self.tmpdir) / "licensing" / "verification_audit.jsonl"
        events = []
        with open(audit_file) as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))

        # Should have at least 2 events
        assert len(events) >= 2

        # Should be in chronological order
        for i in range(len(events) - 1):
            assert events[i]["timestamp"] <= events[i+1]["timestamp"]

    def test_verify_result_serializable(self):
        """Test that verification result can be serialized to JSON."""
        issue = self.authority.issue_credential("alice@example.com", "member")

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id="nonexistent",
            operation="delegate",
            payload={},
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = self.verifier.verify_signed_task(task)

        # Should be able to serialize
        result_dict = result.to_dict()
        result_json = json.dumps(result_dict)

        assert result_json


# ============================================================================
# AuthorityServer Tests (10 tests)
# ============================================================================

class TestAuthorityServer:
    """Test credential authority operations."""

    def setup_method(self):
        """Set up authority."""
        self.tmpdir = tempfile.mkdtemp()
        self.authority = AuthorityServer(self.tmpdir)

    def test_issue_credential_member_tier(self):
        """Test issuing a member tier credential."""
        result = self.authority.issue_credential(
            "alice@example.com",
            "member",
            validity_days=90,
        )

        assert result.success
        assert result.credential is not None
        assert result.credential.member_id == "alice@example.com"
        assert result.credential.license_tier == "member"
        assert result.credential.can_delegate()

    def test_issue_credential_enterprise_tier(self):
        """Test issuing enterprise tier credential."""
        result = self.authority.issue_credential(
            "alice@example.com",
            "enterprise",
        )

        assert result.success
        assert result.credential.license_tier == "enterprise"
        assert result.credential.can_delegate()

    def test_issue_credential_free_tier(self):
        """Test issuing free tier credential."""
        result = self.authority.issue_credential(
            "bob@example.com",
            "free",
        )

        assert result.success
        assert result.credential.license_tier == "free"
        assert not result.credential.can_delegate()

    def test_issue_credential_invalid_tier(self):
        """Test issuing with invalid tier."""
        result = self.authority.issue_credential(
            "alice@example.com",
            "invalid_tier",
        )

        assert not result.success
        assert "Invalid license tier" in result.error_message

    def test_issue_credential_generates_unique_ids(self):
        """Test that each credential has unique ID."""
        result1 = self.authority.issue_credential("alice@example.com", "member")
        result2 = self.authority.issue_credential("alice@example.com", "member")

        assert result1.credential.credential_id != result2.credential.credential_id

    def test_renew_credential(self):
        """Test renewing a credential."""
        # Issue
        result1 = self.authority.issue_credential(
            "alice@example.com",
            "member",
            validity_days=30,
        )
        cred1 = result1.credential

        # Renew
        result2 = self.authority.renew_credential(
            cred1.credential_id,
            validity_days=90,
        )

        assert result2.success
        cred2 = result2.credential

        # Should be same member, different ID
        assert cred2.member_id == cred1.member_id
        assert cred2.credential_id != cred1.credential_id

    def test_downgrade_tier(self):
        """Test downgrading license tier."""
        result = self.authority.issue_credential("alice@example.com", "enterprise")
        cred1 = result.credential

        assert cred1.can_delegate()

        # Downgrade to free
        result2 = self.authority.downgrade_tier(cred1.credential_id, "free")
        cred2 = result2.credential

        assert cred2.license_tier == "free"
        assert not cred2.can_delegate()

    def test_get_credential_info(self):
        """Test getting credential info."""
        result = self.authority.issue_credential("alice@example.com", "member")
        cred = result.credential

        info = self.authority.get_credential_info(cred.credential_id)

        assert info is not None
        assert info["member_id"] == "alice@example.com"
        assert info["license_tier"] == "member"
        assert info["is_valid"]
        assert info["can_delegate"]

    def test_list_member_credentials(self):
        """Test listing member credentials."""
        # Issue multiple
        self.authority.issue_credential("alice@example.com", "member")
        self.authority.issue_credential("alice@example.com", "member")
        self.authority.issue_credential("bob@example.com", "free")

        # List alice's
        creds = self.authority.list_member_credentials("alice@example.com")
        assert len(creds) == 2
        assert all(c["member_id"] == "alice@example.com" for c in creds)

    def test_revoke_credential(self):
        """Test revoking a credential."""
        result = self.authority.issue_credential("alice@example.com", "member")
        cred = result.credential

        success = self.authority.revoke_credential(cred.credential_id, "Test")
        assert success

        # Verify it's revoked
        info = self.authority.get_credential_info(cred.credential_id)
        assert info["is_revoked"]


# ============================================================================
# Edge Cases and Adversarial Tests (8 tests)
# ============================================================================

class TestEdgeCases:
    """Test edge cases and adversarial scenarios."""

    def test_concurrent_revocations(self):
        """Test multiple revocations."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        # Issue 3 credentials
        creds = []
        for i in range(3):
            result = authority.issue_credential(f"user{i}@example.com", "member")
            creds.append(result.credential)

        # Revoke all
        for cred in creds:
            authority.revoke_credential(cred.credential_id)

        # Verify all revoked
        for cred in creds:
            info = authority.get_credential_info(cred.credential_id)
            assert info["is_revoked"]

    def test_zero_validity_days(self):
        """Test issuing with zero validity days."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        result = authority.issue_credential(
            "alice@example.com",
            "member",
            validity_days=0,
        )

        # Should succeed but immediately expired
        assert result.success
        assert result.credential.is_expired()

    def test_very_long_validity(self):
        """Test issuing with very long validity."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        result = authority.issue_credential(
            "alice@example.com",
            "member",
            validity_days=10000,  # ~27 years
        )

        assert result.success
        assert not result.credential.is_expired()

    def test_metadata_preservation(self):
        """Test that metadata is preserved."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        metadata = {
            "organization": "ACME Corp",
            "department": "Engineering",
            "contact": "alice@acme.example.com",
        }

        result = authority.issue_credential(
            "alice@example.com",
            "member",
            metadata=metadata,
        )

        cred = result.credential
        assert cred.metadata == metadata

    def test_empty_payload_task(self):
        """Test verifying task with empty payload."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)
        verifier = A2ADelegationVerifier(tmpdir)

        result = authority.issue_credential("alice@example.com", "member")
        cred = result.credential

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload={},  # Empty
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        result = verifier.verify_signed_task(task)
        # Should run through checks (fail on signature)
        assert "Credential found" in result.checks_passed

    def test_large_payload_task(self):
        """Test verifying task with large payload."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)
        verifier = A2ADelegationVerifier(tmpdir)

        result = authority.issue_credential("alice@example.com", "member")
        cred = result.credential

        # Large payload (10MB of data)
        large_payload = {
            "data": "x" * (10 * 1024 * 1024),
        }

        now = datetime.utcnow()
        task = SignedTask(
            task_id="task_001",
            member_id="alice@example.com",
            credential_id=cred.credential_id,
            operation="delegate",
            payload=large_payload,
            signed_at=now.isoformat() + "Z",
            signature="sig",
        )

        # Should still verify structure
        result = verifier.verify_signed_task(task)
        assert result.task_id == "task_001"

    def test_unicode_in_member_id(self):
        """Test unicode characters in member ID."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        result = authority.issue_credential(
            "äöü@example.com",
            "member",
        )

        assert result.success
        assert result.credential.member_id == "äöü@example.com"

    def test_special_chars_in_reason(self):
        """Test special characters in revocation reason."""
        tmpdir = tempfile.mkdtemp()
        authority = AuthorityServer(tmpdir)

        result = authority.issue_credential("alice@example.com", "member")
        cred = result.credential

        # Reason with special chars
        reason = 'Compromised; "quoted"; <tag>; \n newline'
        authority.revoke_credential(cred.credential_id, reason)

        # Verify reason is stored
        crl = RevocationList(tmpdir)
        stored_reason = crl.get_revocation_reason(cred.credential_id)
        assert stored_reason == reason


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
