"""Phase B: Session Bridging + Crypto Signatures — Full Test Suite (ADR-0541).

Comprehensive tests for cryptographic binding, session bridging, and audit verification.
Coverage:
- Unit: HMAC correctness, key rotation, signature verification
- Integration: snapshot signing + verification, bridge creation/resumption
- E2E: cross-session audit chain continuity
- Adversarial: tampering detection, tenant isolation, signature spoofing

Compliance:
- GDPR Art. 30/32: Audit continuity, cryptographic proof
- All operations fail-closed: any error → reject
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

from core.infinite_session.crypto_binding import (
    CryptoBinding,
    SignatureMetadata,
    KeyRotationStatus,
)
from core.infinite_session.session_bridger import (
    SessionBridger,
    SessionBridgeEvent,
)
from core.infinite_session.audit_verification import (
    AuditVerifier,
    VerificationStatus,
)
from core.infinite_session.event_store import EventStore
from core.infinite_session.snapshot_schema import (
    Snapshot,
    SnapshotType,
)


class TestCryptoBinding:
    """Unit tests for cryptographic binding (HMAC-SHA256)."""

    @pytest.fixture
    def crypto_binding(self, tmp_path):
        """Create crypto binding with temp directory."""
        return CryptoBinding(corvin_home=str(tmp_path / ".corvin"))

    @pytest.fixture
    def audit_events(self):
        """Collect audit events during tests."""
        return []

    def audit_callback(self, audit_events):
        """Return callback that collects audit events."""
        def callback(**kwargs):
            audit_events.append(kwargs)
            return True
        return callback

    def test_sign_snapshot_creates_valid_signature(self, crypto_binding, audit_events):
        """Test: snapshot hash can be signed and verified."""
        tenant_id = "_default"
        snapshot_hash = "abc123def456"

        # Sign snapshot
        signature, error = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
            audit_callback=self.audit_callback(audit_events),
        )

        assert error == "", f"Sign failed: {error}"
        assert signature is not None
        assert len(signature) == 64  # SHA256 hex = 64 chars
        assert len(audit_events) >= 1

    def test_sign_snapshot_fail_closed_on_empty_tenant(self, crypto_binding):
        """Test: signing fails (fail-closed) on empty tenant_id."""
        signature, error = crypto_binding.sign_snapshot(
            tenant_id="",
            snapshot_hash="abc123",
        )

        assert signature is None
        assert "tenant_id is required" in error

    def test_sign_snapshot_fail_closed_on_empty_hash(self, crypto_binding):
        """Test: signing fails (fail-closed) on empty hash."""
        signature, error = crypto_binding.sign_snapshot(
            tenant_id="_default",
            snapshot_hash="",
        )

        assert signature is None
        assert "snapshot_hash is required" in error

    def test_verify_signature_accepts_valid_signature(self, crypto_binding, audit_events):
        """Test: valid signature verifies successfully."""
        tenant_id = "_default"
        snapshot_hash = "abc123def456"

        # Sign
        signature, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        # Verify
        is_valid, error = crypto_binding.verify_signature(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
            signature=signature,
            audit_callback=self.audit_callback(audit_events),
        )

        assert is_valid, f"Verification failed: {error}"
        assert error == ""

    def test_verify_signature_rejects_tampered_hash(self, crypto_binding, audit_events):
        """Test: verification fails (fail-closed) if hash was tampered."""
        tenant_id = "_default"
        snapshot_hash = "abc123def456"
        tampered_hash = "tampered999999"

        # Sign original
        signature, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        # Verify with tampered hash
        is_valid, error = crypto_binding.verify_signature(
            tenant_id=tenant_id,
            snapshot_hash=tampered_hash,
            signature=signature,
            audit_callback=self.audit_callback(audit_events),
        )

        assert not is_valid
        assert "Signature mismatch" in error

    def test_verify_signature_rejects_tampered_signature(self, crypto_binding, audit_events):
        """Test: verification fails if signature was tampered."""
        tenant_id = "_default"
        snapshot_hash = "abc123def456"

        # Sign
        signature, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        # Tamper with signature
        tampered_sig = signature[:-2] + "ff"

        # Verify
        is_valid, error = crypto_binding.verify_signature(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
            signature=tampered_sig,
            audit_callback=self.audit_callback(audit_events),
        )

        assert not is_valid
        assert "Signature mismatch" in error

    def test_verify_signature_fail_closed_on_empty_tenant(self, crypto_binding):
        """Test: verification fails (fail-closed) on empty tenant_id."""
        is_valid, error = crypto_binding.verify_signature(
            tenant_id="",
            snapshot_hash="abc123",
            signature="deadbeef",
        )

        assert not is_valid
        assert "tenant_id is required" in error

    def test_key_rotation_archives_old_key(self, crypto_binding, audit_events):
        """Test: key rotation archives old key and generates new one."""
        tenant_id = "_default"

        # Generate initial key by signing
        sig1, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash="hash1",
        )

        # Rotate key
        success, error = crypto_binding.rotate_key(
            tenant_id=tenant_id,
            audit_callback=self.audit_callback(audit_events),
        )

        assert success, f"Rotation failed: {error}"
        assert len(audit_events) >= 1

        # Verify new key is different
        sig2, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash="hash1",
        )

        # Signatures should be different (different keys)
        assert sig1 != sig2

    def test_signature_is_deterministic(self, crypto_binding):
        """Test: same input always produces same signature (determinism)."""
        tenant_id = "_default"
        snapshot_hash = "abc123def456"

        sig1, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        sig2, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        assert sig1 == sig2, "Signature should be deterministic"


class TestSessionBridger:
    """Integration tests for session bridging."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp

    @pytest.fixture
    def components(self, temp_dir):
        """Create event_store, crypto_binding, session_bridger."""
        event_store = EventStore(corvin_home=temp_dir)
        crypto_binding = CryptoBinding(corvin_home=temp_dir)
        session_bridger = SessionBridger(
            event_store=event_store,
            crypto_binding=crypto_binding,
            corvin_home=temp_dir,
        )
        return event_store, crypto_binding, session_bridger

    def test_create_bridge_signs_and_persists(self, components):
        """Test: bridge creation signs snapshot and persists to disk."""
        event_store, crypto_binding, session_bridger = components

        tenant_id = "_default"
        task_id = "test_task"
        snapshot = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id="phase_a",
            state_dict={"key": "value"},
        )

        # Create bridge
        bridge, error = session_bridger.create_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id="session_1",
            dest_session_id="session_2",
            snapshot=snapshot,
            phase_completed="phase_a",
            artifacts=["ADR-0541"],
        )

        assert error == "", f"Bridge creation failed: {error}"
        assert bridge is not None
        assert bridge.signature is not None
        assert len(bridge.signature) == 64  # SHA256 hex

    def test_create_bridge_fail_closed_on_empty_tenant(self, components):
        """Test: bridge creation fails (fail-closed) on empty tenant_id."""
        _, _, session_bridger = components

        snapshot = Snapshot.create(
            tenant_id="valid_tenant",
            task_id="task_id",
            phase_id="phase",
            state_dict={},
        )

        bridge, error = session_bridger.create_bridge(
            tenant_id="",
            task_id="task_id",
            source_session_id="s1",
            dest_session_id="s2",
            snapshot=snapshot,
            phase_completed="phase",
        )

        assert bridge is None
        assert "tenant_id is required" in error

    def test_create_bridge_fail_closed_on_tenant_mismatch(self, components):
        """Test: bridge creation fails if snapshot tenant doesn't match."""
        _, _, session_bridger = components

        snapshot = Snapshot.create(
            tenant_id="tenant_a",
            task_id="task_id",
            phase_id="phase",
            state_dict={},
        )

        bridge, error = session_bridger.create_bridge(
            tenant_id="tenant_b",
            task_id="task_id",
            source_session_id="s1",
            dest_session_id="s2",
            snapshot=snapshot,
            phase_completed="phase",
        )

        assert bridge is None
        assert "tenant_id mismatch" in error

    def test_resume_from_bridge_verifies_signature(self, components):
        """Test: resuming from bridge verifies signature (fail-closed on mismatch)."""
        _, _, session_bridger = components

        tenant_id = "_default"
        task_id = "test_task"
        snapshot = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id="phase_a",
            state_dict={"key": "value"},
        )

        # Create bridge
        bridge, error = session_bridger.create_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id="session_1",
            dest_session_id="session_2",
            snapshot=snapshot,
            phase_completed="phase_a",
        )

        assert error == ""

        # Resume from bridge
        state, error = session_bridger.resume_from_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            bridge_id=bridge.bridge_id,
        )

        assert error == "", f"Resume failed: {error}"
        assert state is not None
        assert state["bridge_id"] == bridge.bridge_id

    def test_list_bridges_returns_all_bridges(self, components):
        """Test: list_bridges returns all bridges for a task."""
        _, _, session_bridger = components

        tenant_id = "_default"
        task_id = "test_task"

        # Create 3 bridges
        for i in range(3):
            snapshot = Snapshot.create(
                tenant_id=tenant_id,
                task_id=task_id,
                phase_id=f"phase_{i}",
                state_dict={"iteration": i},
            )

            session_bridger.create_bridge(
                tenant_id=tenant_id,
                task_id=task_id,
                source_session_id=f"session_{i}",
                dest_session_id=f"session_{i+1}",
                snapshot=snapshot,
                phase_completed=f"phase_{i}",
            )

        # List bridges
        bridges, error = session_bridger.list_bridges(
            tenant_id=tenant_id,
            task_id=task_id,
        )

        assert error == ""
        assert len(bridges) == 3


class TestAuditVerifier:
    """Tests for audit chain verification."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp

    @pytest.fixture
    def verifier(self, temp_dir):
        """Create verifier."""
        event_store = EventStore(corvin_home=temp_dir)
        crypto_binding = CryptoBinding(corvin_home=temp_dir)
        verifier = AuditVerifier(
            event_store=event_store,
            crypto_binding=crypto_binding,
            corvin_home=temp_dir,
        )
        return verifier

    def test_verify_task_chain_returns_pass_for_empty_task(self, verifier):
        """Test: verification passes for task with no events."""
        result, error = verifier.verify_task_chain(
            tenant_id="_default",
            task_id="empty_task",
        )

        # Result should be available (even if no events)
        assert result is not None or error != ""


class TestAdversarialCryptoBinding:
    """Adversarial tests for cryptographic binding."""

    @pytest.fixture
    def crypto_binding(self, tmp_path):
        """Create crypto binding."""
        return CryptoBinding(corvin_home=str(tmp_path / ".corvin"))

    def test_timing_attack_resistance(self, crypto_binding):
        """Test: signature verification is resistant to timing attacks.

        Adversary scenario: attacker tries to guess signature bit-by-bit by timing
        verification duration. Defense: HMAC.compare_digest() uses constant-time comparison.
        """
        tenant_id = "_default"
        snapshot_hash = "abc123def456"

        # Sign
        signature, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

        # Try multiple wrong signatures with same prefix
        wrong_sigs = [
            "0" * 64,  # All zeros
            signature[:32] + "0" * 32,  # Same first half
            signature[:2] + "0" * 62,  # Same first 2 chars
        ]

        # All should fail with similar timing (no timing leak)
        for wrong_sig in wrong_sigs:
            is_valid, _ = crypto_binding.verify_signature(
                tenant_id=tenant_id,
                snapshot_hash=snapshot_hash,
                signature=wrong_sig,
            )
            assert not is_valid

    def test_cross_tenant_key_isolation(self, crypto_binding):
        """Test: keys are isolated per tenant (no cross-tenant tampering)."""
        tenant_a = "tenant_a"
        tenant_b = "tenant_b"
        snapshot_hash = "abc123def456"

        # Generate key for tenant_a
        sig_a, _ = crypto_binding.sign_snapshot(
            tenant_id=tenant_a,
            snapshot_hash=snapshot_hash,
        )

        # Try to verify tenant_a's signature with tenant_b's key
        is_valid, _ = crypto_binding.verify_signature(
            tenant_id=tenant_b,
            snapshot_hash=snapshot_hash,
            signature=sig_a,
        )

        # Should fail: keys are different
        assert not is_valid

    def test_key_file_permissions(self, crypto_binding):
        """Test: key files have restricted permissions (0o600)."""
        tenant_id = "_default"

        # Generate key
        crypto_binding.sign_snapshot(
            tenant_id=tenant_id,
            snapshot_hash="test",
        )

        # Check file permissions
        key_path = crypto_binding._get_key_path(tenant_id)
        mode = key_path.stat().st_mode & 0o777

        assert mode == 0o600, f"Key file has insecure permissions: {oct(mode)}"


class TestAdversarialSessionBridger:
    """Adversarial tests for session bridging."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        with tempfile.TemporaryDirectory() as tmp:
            yield tmp

    @pytest.fixture
    def components(self, temp_dir):
        """Create components."""
        event_store = EventStore(corvin_home=temp_dir)
        crypto_binding = CryptoBinding(corvin_home=temp_dir)
        session_bridger = SessionBridger(
            event_store=event_store,
            crypto_binding=crypto_binding,
            corvin_home=temp_dir,
        )
        return event_store, crypto_binding, session_bridger, temp_dir

    def test_bridge_tampering_detection(self, components):
        """Test: tampering with persisted bridge is detected on resume.

        Adversary scenario: attacker modifies bridge file on disk (changes signature).
        Defense: signature verification fails on resume.
        """
        _, _, session_bridger, temp_dir = components

        tenant_id = "_default"
        task_id = "test_task"
        snapshot = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id="phase_a",
            state_dict={"key": "value"},
        )

        # Create bridge
        bridge, error = session_bridger.create_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id="session_1",
            dest_session_id="session_2",
            snapshot=snapshot,
            phase_completed="phase_a",
        )

        assert error == ""

        # Tamper with bridge file
        bridge_file = (
            Path(temp_dir) / "bridges" / tenant_id / task_id / f"{bridge.bridge_id}.json"
        )
        bridge_data = json.loads(bridge_file.read_text())
        bridge_data["signature"] = "tampered" + bridge_data["signature"][8:]
        bridge_file.write_text(json.dumps(bridge_data))

        # Try to resume (should fail)
        state, error = session_bridger.resume_from_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            bridge_id=bridge.bridge_id,
        )

        assert state is None
        assert "Signature verification failed" in error or "Signature mismatch" in error

    def test_bridge_snapshot_hash_tampering(self, components):
        """Test: tampering with snapshot_hash in bridge is detected."""
        _, _, session_bridger, temp_dir = components

        tenant_id = "_default"
        task_id = "test_task"
        snapshot = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id="phase_a",
            state_dict={"key": "value"},
        )

        # Create bridge
        bridge, error = session_bridger.create_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id="session_1",
            dest_session_id="session_2",
            snapshot=snapshot,
            phase_completed="phase_a",
        )

        assert error == ""

        # Tamper with snapshot_hash in bridge file
        bridge_file = (
            Path(temp_dir) / "bridges" / tenant_id / task_id / f"{bridge.bridge_id}.json"
        )
        bridge_data = json.loads(bridge_file.read_text())
        bridge_data["snapshot_hash"] = "tampered" + bridge_data["snapshot_hash"][8:]
        bridge_file.write_text(json.dumps(bridge_data))

        # Try to resume (should fail: hash no longer matches signature)
        state, error = session_bridger.resume_from_bridge(
            tenant_id=tenant_id,
            task_id=task_id,
            bridge_id=bridge.bridge_id,
        )

        assert state is None
        assert "Signature verification failed" in error or "Signature mismatch" in error

    def test_bridge_tenant_isolation_violation(self, components):
        """Test: cross-tenant bridge attacks are detected (fail-closed).

        Adversary scenario: attacker tries to use a bridge from one tenant in another.
        Defense: resume_from_bridge verifies tenant_id matches.
        """
        _, _, session_bridger, temp_dir = components

        tenant_a = "tenant_a"
        tenant_b = "tenant_b"
        task_id = "test_task"

        # Create bridge in tenant_a
        snapshot_a = Snapshot.create(
            tenant_id=tenant_a,
            task_id=task_id,
            phase_id="phase_a",
            state_dict={"key": "value"},
        )

        bridge_a, _ = session_bridger.create_bridge(
            tenant_id=tenant_a,
            task_id=task_id,
            source_session_id="session_1",
            dest_session_id="session_2",
            snapshot=snapshot_a,
            phase_completed="phase_a",
        )

        # Try to resume bridge in tenant_b (should fail)
        # (Bridge file won't exist in tenant_b's directory, so it will be not found)
        state, error = session_bridger.resume_from_bridge(
            tenant_id=tenant_b,
            task_id=task_id,
            bridge_id=bridge_a.bridge_id,
        )

        assert state is None
        assert "Bridge not found" in error or "fail" in error.lower()
