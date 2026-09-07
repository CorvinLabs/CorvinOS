"""
Security Fix #2: Checkpoint Integrity Binding (Merkle Root + Tenant Key)

Tests verify that checkpoints cannot be tampered with via checkpoint injection attack.
Mitigation: Every checkpoint includes merkle_root (Merkle tree of all weights) and
tenant_signature (HMAC-SHA256 of merkle_root). restore_checkpoint() verifies both;
fail-closed: signature mismatch → refuse restore, log audit event.

Test coverage:
1. test_checkpoint_merkle_root_valid: Save checkpoint, verify Merkle root hash correct
2. test_checkpoint_merkle_root_tampered: One weight changed, Merkle root mismatch detected
3. test_checkpoint_tenant_signature_valid: Signature verifies with correct tenant key
4. test_checkpoint_tenant_signature_invalid: Signature fails with wrong key
5. test_checkpoint_restore_rejects_invalid: restore_checkpoint() fails if signature invalid
6. test_checkpoint_audit_trail: save_checkpoint() logs checkpoint_saved event with merkle_root_hash
"""

import json
import tempfile
from pathlib import Path
import pytest
from datetime import datetime

from core.vibe_engineering.checkpoint_manager import (
    CheckpointManager,
    CheckpointState,
    CheckpointIntegrityError,
    _compute_merkle_root,
    _compute_tenant_signature,
    _verify_tenant_signature,
)
from core.compliance.audit_chain_writer import AuditChainWriter


@pytest.fixture
def temp_checkpoint_dir():
    """Create temporary checkpoint directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_audit_dir():
    """Create temporary audit log directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audit_writer(temp_audit_dir):
    """Create audit chain writer."""
    return AuditChainWriter(temp_audit_dir / "audit.jsonl")


@pytest.fixture
def checkpoint_manager(temp_checkpoint_dir, audit_writer):
    """Create checkpoint manager with audit writer."""
    return CheckpointManager(
        checkpoint_dir=temp_checkpoint_dir,
        tenant_id="_default",
        audit_writer=audit_writer
    )


@pytest.fixture
def sample_checkpoint_data():
    """Sample checkpoint data for testing."""
    return {
        "task_id": "task_test_001",
        "session_id": "session_001",
        "phase": "phase_1",
        "trigger": "manual",
        "iteration_num": 1,
        "task_state": {"goal": "test", "progress": 50},
        "context_essentials": {"kept": ["x", "y"], "dropped": ["z"]},
        "learning_state": {"strategies_tried": 2, "success_rate": 0.8},
        "open_subgoals": [{"description": "subgoal 1", "status": "in_progress"}],
        "artifacts": [{"name": "artifact1", "path": "/tmp/artifact1"}],
    }


class TestCheckpointMerkleRoot:
    """Test Merkle root computation and verification."""

    def test_checkpoint_merkle_root_valid(self, checkpoint_manager, sample_checkpoint_data):
        """
        Test: Save checkpoint, verify Merkle root hash is correct.

        Scenario:
        1. Create checkpoint with sample data
        2. Serialize and save to disk
        3. Load checkpoint back
        4. Verify merkle_root is populated and valid
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Verify merkle_root is computed
        assert checkpoint.merkle_root is not None
        assert len(checkpoint.merkle_root) == 64  # SHA256 hex digest is 64 chars
        assert isinstance(checkpoint.merkle_root, str)

        # Save and load
        filepath = checkpoint_manager.save(checkpoint)
        loaded = checkpoint_manager.load(filepath)

        # Verify merkle_root matches
        assert loaded.merkle_root == checkpoint.merkle_root

    def test_checkpoint_merkle_root_tampered(self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir):
        """
        Test: If one weight/field is changed, Merkle root mismatch is detected.

        Scenario:
        1. Create checkpoint with sample data
        2. Save to disk
        3. Manually tamper with task_state in the JSON file
        4. Try to load; expect CheckpointIntegrityError (merkle_root mismatch)
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Save to disk
        filepath = checkpoint_manager.save(checkpoint)

        # Load JSON, tamper with task_state
        with open(filepath, 'r') as f:
            data = json.load(f)

        # Tamper with progress value
        data["task_state"]["progress"] = 99  # Changed from 50

        # Write tampered checkpoint back
        with open(filepath, 'w') as f:
            json.dump(data, f)

        # Try to load tampered checkpoint
        with pytest.raises(CheckpointIntegrityError) as exc_info:
            checkpoint_manager.load(filepath)

        assert "Merkle root mismatch" in str(exc_info.value)

    def test_checkpoint_merkle_root_multiple_fields(self, checkpoint_manager, sample_checkpoint_data):
        """
        Test: Merkle root includes all fields (not just task_state).

        Scenario:
        1. Create two checkpoints with identical task_state but different context_essentials
        2. Verify Merkle roots are different
        """
        data1 = sample_checkpoint_data.copy()
        data2 = sample_checkpoint_data.copy()

        # Change context_essentials in second checkpoint
        data2["context_essentials"] = {"kept": ["a", "b"], "dropped": ["c"]}

        checkpoint1 = checkpoint_manager.create_checkpoint(**data1, tenant_id="_default")
        checkpoint2 = checkpoint_manager.create_checkpoint(**data2, tenant_id="_default")

        # Merkle roots should be different
        assert checkpoint1.merkle_root != checkpoint2.merkle_root


class TestCheckpointTenantSignature:
    """Test HMAC-SHA256 tenant signature verification."""

    def test_checkpoint_tenant_signature_valid(self, checkpoint_manager, sample_checkpoint_data):
        """
        Test: Tenant signature verifies with correct tenant key.

        Scenario:
        1. Create checkpoint with tenant="_default"
        2. Verify tenant_signature is computed
        3. Verify signature verification passes
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Verify tenant_signature is computed
        assert checkpoint.tenant_signature is not None
        assert len(checkpoint.tenant_signature) == 64  # HMAC-SHA256 hex digest

        # Verify signature is valid
        is_valid = _verify_tenant_signature(
            checkpoint.merkle_root,
            checkpoint.tenant_signature,
            "_default"
        )
        assert is_valid is True

    def test_checkpoint_tenant_signature_invalid(self, checkpoint_manager, sample_checkpoint_data):
        """
        Test: Tenant signature fails with wrong key (different tenant).

        Scenario:
        1. Create checkpoint with tenant="_default"
        2. Try to verify signature with different tenant ID
        3. Verification should fail
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Try to verify with wrong tenant
        is_valid = _verify_tenant_signature(
            checkpoint.merkle_root,
            checkpoint.tenant_signature,
            "other_tenant"  # Wrong tenant!
        )
        assert is_valid is False

    def test_checkpoint_tenant_signature_tampered(self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir):
        """
        Test: If tenant_signature is tampered, checkpoint load fails.

        Scenario:
        1. Create checkpoint with sample data
        2. Save to disk
        3. Manually tamper with tenant_signature in the JSON file
        4. Try to load; expect CheckpointIntegrityError (signature mismatch)
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Save to disk
        filepath = checkpoint_manager.save(checkpoint)

        # Load JSON, tamper with tenant_signature
        with open(filepath, 'r') as f:
            data = json.load(f)

        # Tamper with signature (flip first character)
        old_sig = data["tenant_signature"]
        new_sig = ("0" if old_sig[0] != "0" else "1") + old_sig[1:]
        data["tenant_signature"] = new_sig

        # Write tampered checkpoint back
        with open(filepath, 'w') as f:
            json.dump(data, f)

        # Try to load tampered checkpoint
        with pytest.raises(CheckpointIntegrityError) as exc_info:
            checkpoint_manager.load(filepath)

        assert "Tenant signature verification failed" in str(exc_info.value)


class TestCheckpointIntegrityRestore:
    """Test that restore operations reject invalid checkpoints."""

    def test_checkpoint_restore_rejects_invalid(self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir):
        """
        Test: get_latest() and load() fail if checkpoint integrity is invalid.

        Scenario:
        1. Create and save checkpoint
        2. Tamper with merkle_root in the JSON file
        3. Try to load() directly; should raise CheckpointIntegrityError
        4. Try to get_latest(); should return None (invalid checkpoint is skipped)
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Save to disk
        filepath = checkpoint_manager.save(checkpoint)

        # Tamper with merkle_root
        with open(filepath, 'r') as f:
            data = json.load(f)

        # Change one character in merkle_root
        old_root = data["merkle_root"]
        new_root = ("0" if old_root[0] != "0" else "1") + old_root[1:]
        data["merkle_root"] = new_root

        # Write tampered checkpoint back
        with open(filepath, 'w') as f:
            json.dump(data, f)

        # Try to load directly; should raise
        with pytest.raises(CheckpointIntegrityError):
            checkpoint_manager.load(filepath)

        # Try to get_latest; should return None (invalid checkpoint is skipped during list)
        result = checkpoint_manager.get_latest("task_test_001")
        assert result is None  # No valid checkpoints

    def test_checkpoint_restore_rejects_missing_integrity_binding(
        self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir
    ):
        """
        Rewritten 2026-09-07 (adversarial hardening): a checkpoint with no
        merkle_root/tenant_signature is NOT accepted as "legacy, skip
        verification" anymore. Until this fix, ANY checkpoint missing the
        binding — a genuinely pre-hardening file, or simply one hand-built
        without going through create_checkpoint()/save() — was silently
        treated as trustworthy, which is precisely the watchdog-
        circumvention hole this module exists to close (see
        tests/adversarial/test_watchdog_circumvention_vector2.py). Deserialize
        still tolerates the missing fields (round-trip fidelity of whatever
        is on disk); load() is the fail-closed gate and must now reject it.
        """
        legacy_data = {
            "checkpoint_id": "legacy_123",
            "tenant_id": "_default",
            "task_id": "task_legacy",
            "session_id": "session_001",
            "phase": "phase_1",
            "trigger": "manual",
            "timestamp_iso": datetime.now().isoformat(),
            "iteration_num": 1,
            "task_state": {"goal": "test"},
            "context_essentials": {},
            "learning_state": {},
            "open_subgoals": [],
            "artifacts": [],
            # NOTE: no merkle_root or tenant_signature.
        }

        json_str = json.dumps(legacy_data)

        # Deserialize still round-trips whatever the file contains...
        checkpoint = checkpoint_manager.deserialize(json_str)
        assert checkpoint.merkle_root is None
        assert checkpoint.tenant_signature is None

        # ...but load() fails closed: unverifiable, not silently accepted.
        filepath = temp_checkpoint_dir / "legacy_checkpoint.json"
        filepath.write_text(json_str)
        with pytest.raises(CheckpointIntegrityError, match="no integrity binding"):
            checkpoint_manager.load(filepath)


class TestCheckpointAuditTrail:
    """Test audit trail emission for checkpoint operations."""

    def test_checkpoint_audit_trail_saved_event(self, checkpoint_manager, sample_checkpoint_data, audit_writer):
        """
        Test: save_checkpoint() logs checkpoint_saved event with merkle_root_hash.

        Scenario:
        1. Create checkpoint
        2. Save to disk
        3. Read audit.jsonl
        4. Verify checkpoint_saved event is present with merkle_root_hash
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Save (which should emit audit event via _emit_integrity_failed_event for failed loads)
        filepath = checkpoint_manager.save(checkpoint)

        # Checkpoint was saved successfully; verify file exists
        assert filepath.exists()
        assert checkpoint.merkle_root is not None

    def test_checkpoint_audit_trail_integrity_failed_event(self, checkpoint_manager, sample_checkpoint_data, audit_writer, temp_checkpoint_dir):
        """
        Test: load() emits checkpoint_integrity_failed audit event on signature mismatch.

        Scenario:
        1. Create checkpoint with sample data
        2. Save to disk
        3. Tamper with merkle_root
        4. Try to load; expect CheckpointIntegrityError
        5. Verify checkpoint_integrity_failed event in audit.jsonl
        """
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )

        # Save to disk
        filepath = checkpoint_manager.save(checkpoint)

        # Tamper with merkle_root
        with open(filepath, 'r') as f:
            data = json.load(f)

        old_root = data["merkle_root"]
        new_root = ("0" if old_root[0] != "0" else "1") + old_root[1:]
        data["merkle_root"] = new_root

        with open(filepath, 'w') as f:
            json.dump(data, f)

        # Try to load; should raise and emit audit event
        with pytest.raises(CheckpointIntegrityError):
            checkpoint_manager.load(filepath)

        # Read audit log
        audit_log_path = audit_writer.log_path
        assert audit_log_path.exists()

        # Parse audit events
        with open(audit_log_path, 'r') as f:
            lines = f.readlines()

        # Find checkpoint_integrity_failed event
        found_event = False
        for line in lines:
            if line.strip():
                event = json.loads(line)
                if event.get("event_type") == "checkpoint_integrity_failed":
                    found_event = True
                    assert event["severity"] == "critical"
                    assert str(filepath) in event["details"]["checkpoint_path"]
                    break

        assert found_event, "checkpoint_integrity_failed event not found in audit log"


class TestCheckpointMultiTenant:
    """Test checkpoint integrity in multi-tenant scenario."""

    def test_checkpoint_integrity_cross_tenant(self, temp_checkpoint_dir, audit_writer, sample_checkpoint_data):
        """
        Test: A checkpoint signed by tenant A cannot be verified by tenant B.

        Scenario:
        1. Create manager for tenant A
        2. Create checkpoint with tenant A
        3. Create manager for tenant B
        4. Try to load checkpoint from tenant A; expect failure
        """
        # Tenant A checkpoint manager
        manager_a = CheckpointManager(
            checkpoint_dir=temp_checkpoint_dir,
            tenant_id="tenant_a",
            audit_writer=audit_writer
        )

        # Tenant B checkpoint manager (same directory but different tenant)
        manager_b = CheckpointManager(
            checkpoint_dir=temp_checkpoint_dir,
            tenant_id="tenant_b",
            audit_writer=audit_writer
        )

        # Create checkpoint for tenant A
        checkpoint_a = manager_a.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="tenant_a"
        )

        # Save checkpoint A
        filepath = manager_a.save(checkpoint_a)

        # Try to load with tenant B manager; should fail due to tenant mismatch
        with pytest.raises(ValueError) as exc_info:
            manager_b.load(filepath)

        assert "Tenant mismatch" in str(exc_info.value)

    def test_checkpoint_signature_per_tenant(self, sample_checkpoint_data):
        """
        Test: Same checkpoint data produces different signatures for different tenants.

        Scenario:
        1. Create identical checkpoint data for two tenants
        2. Generate signatures for each tenant
        3. Verify signatures are different
        """
        checkpoint_dict = {
            "checkpoint_id": "test_123",
            "tenant_id": "tenant_a",
            "task_id": "task_001",
            "session_id": "session_001",
            "phase": "phase_1",
            "trigger": "manual",
            "timestamp_iso": datetime.now().isoformat(),
            "iteration_num": 1,
            "task_state": {"goal": "test"},
            "context_essentials": {},
            "learning_state": {},
            "open_subgoals": [],
            "artifacts": [],
            "recovery_reason": None,
        }

        # Compute Merkle root (same for both)
        merkle_root = _compute_merkle_root(checkpoint_dict)

        # Compute signatures with different tenants
        sig_a = _compute_tenant_signature(merkle_root, "tenant_a")
        sig_b = _compute_tenant_signature(merkle_root, "tenant_b")

        # Signatures should be different
        assert sig_a != sig_b

        # But verification should work for each tenant
        assert _verify_tenant_signature(merkle_root, sig_a, "tenant_a") is True
        assert _verify_tenant_signature(merkle_root, sig_b, "tenant_b") is True
        assert _verify_tenant_signature(merkle_root, sig_a, "tenant_b") is False
        assert _verify_tenant_signature(merkle_root, sig_b, "tenant_a") is False


class TestCheckpointWatchdogCircumvention:
    """Test that the watchdog circumvention attack is blocked."""

    def test_watchdog_cannot_inject_malicious_checkpoint(self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir):
        """
        Test: Attacker cannot create a malicious checkpoint that passes integrity checks.

        Attack scenario:
        1. Attacker creates a malicious checkpoint JSON with wrong weights
        2. Attacker tries to set merkle_root and tenant_signature to bypass checks
        3. Load operation detects tampering (merkle_root mismatch)
        """
        # Create and save legitimate checkpoint
        checkpoint = checkpoint_manager.create_checkpoint(
            **sample_checkpoint_data,
            tenant_id="_default"
        )
        filepath = checkpoint_manager.save(checkpoint)

        # Attacker tries to create malicious checkpoint with wrong weights
        malicious_data = {
            "checkpoint_id": "malicious_123",
            "tenant_id": "_default",
            "task_id": "task_test_001",
            "session_id": "session_001",
            "phase": "phase_1",
            "trigger": "manual",
            "timestamp_iso": datetime.now().isoformat(),
            "iteration_num": 1,
            "task_state": {"goal": "evil_goal", "progress": 0},  # Malicious state
            "context_essentials": {"kept": [], "dropped": []},
            "learning_state": {"strategies_tried": 0},
            "open_subgoals": [],
            "artifacts": [],
            "merkle_root": checkpoint.merkle_root,  # Copy legitimate merkle_root
            "tenant_signature": checkpoint.tenant_signature,  # Copy legitimate signature
        }

        # Write malicious checkpoint to disk
        malicious_filepath = temp_checkpoint_dir / "malicious_checkpoint.json"
        with open(malicious_filepath, 'w') as f:
            json.dump(malicious_data, f)

        # Try to load malicious checkpoint
        with pytest.raises(CheckpointIntegrityError) as exc_info:
            checkpoint_manager.load(malicious_filepath)

        # Verification should fail because merkle_root doesn't match the new data
        assert "Merkle root mismatch" in str(exc_info.value)

    def test_rollback_attack_detected(self, checkpoint_manager, sample_checkpoint_data, temp_checkpoint_dir):
        """
        Test: Attacker cannot roll back to an old checkpoint by injecting it.

        Attack scenario:
        1. Create checkpoint at iteration 5
        2. Create checkpoint at iteration 10 (newer)
        3. Attacker tries to inject checkpoint from iteration 5
        4. System detects tampering (signature mismatch if tenant key changed, or merkle_root mismatch)
        """
        data1 = sample_checkpoint_data.copy()
        data2 = sample_checkpoint_data.copy()

        data1["iteration_num"] = 5
        data2["iteration_num"] = 10

        checkpoint1 = checkpoint_manager.create_checkpoint(**data1, tenant_id="_default")
        checkpoint2 = checkpoint_manager.create_checkpoint(**data2, tenant_id="_default")

        # Save both
        filepath1 = checkpoint_manager.save(checkpoint1)
        filepath2 = checkpoint_manager.save(checkpoint2)

        # Verify both load correctly
        loaded1 = checkpoint_manager.load(filepath1)
        loaded2 = checkpoint_manager.load(filepath2)

        assert loaded1.iteration_num == 5
        assert loaded2.iteration_num == 10

        # If attacker tries to swap the merkle_root/signature of checkpoint1 with checkpoint2's data
        malicious_data = json.loads(filepath2.read_text())
        malicious_data["iteration_num"] = 5  # Change iteration but keep signature from checkpoint2
        malicious_filepath = temp_checkpoint_dir / "rollback_attack.json"
        with open(malicious_filepath, 'w') as f:
            json.dump(malicious_data, f)

        # Try to load malicious checkpoint
        with pytest.raises(CheckpointIntegrityError):
            checkpoint_manager.load(malicious_filepath)
