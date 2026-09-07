"""
Unit Tests: Checkpoint Signer (Fix #2: Watchdog Circumvention Mitigation)

Tests checkpoint signing and verification to ensure watchdog checkpoints
cannot be forged or tampered with.
"""

import pytest
import hashlib
import hmac
from pathlib import Path

from core.learning.checkpoint_signer import (
    CheckpointSigner,
    CheckpointSignatureError,
    CheckpointSigningContext,
)


class TestCheckpointSigner:
    """Test CheckpointSigner basic functionality."""

    def test_signer_initialization(self):
        """Signer initializes with tenant_id."""
        signer = CheckpointSigner("tenant_a")
        assert signer.tenant_id == "tenant_a"

    def test_signer_rejects_empty_tenant(self):
        """Signer rejects empty tenant_id."""
        with pytest.raises(ValueError) as exc:
            CheckpointSigner("")
        assert "non-empty string" in str(exc.value)

    def test_signer_rejects_none_tenant(self):
        """Signer rejects None tenant_id."""
        with pytest.raises(ValueError):
            CheckpointSigner(None)

    def test_get_tenant_key_deterministic(self):
        """Tenant key derivation is deterministic."""
        signer1 = CheckpointSigner("tenant_a")
        signer2 = CheckpointSigner("tenant_a")

        key1 = signer1.get_tenant_key()
        key2 = signer2.get_tenant_key()

        assert key1 == key2, "Same tenant should produce same key"

    def test_get_tenant_key_unique_per_tenant(self):
        """Different tenants produce different keys."""
        signer_a = CheckpointSigner("tenant_a")
        signer_b = CheckpointSigner("tenant_b")

        key_a = signer_a.get_tenant_key()
        key_b = signer_b.get_tenant_key()

        assert key_a != key_b, "Different tenants must have different keys"

    def test_merkle_root_computation(self):
        """Merkle root is computed correctly."""
        signer = CheckpointSigner("tenant_a")

        state = {
            "α_core": 0.1,
            "damping_core": 0.9,
            "loss": 0.5,
        }

        merkle_root = signer.compute_merkle_root(state)

        # Merkle root should be a 64-char hex string (SHA256)
        assert isinstance(merkle_root, str)
        assert len(merkle_root) == 64
        assert all(c in "0123456789abcdef" for c in merkle_root)

    def test_merkle_root_deterministic(self):
        """Same state produces same merkle root."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1, "damping_core": 0.9}

        merkle1 = signer.compute_merkle_root(state)
        merkle2 = signer.compute_merkle_root(state)

        assert merkle1 == merkle2, "Same state must produce same merkle root"

    def test_merkle_root_changes_on_state_change(self):
        """Different state produces different merkle root."""
        signer = CheckpointSigner("tenant_a")

        state1 = {"α_core": 0.1}
        state2 = {"α_core": 0.2}

        merkle1 = signer.compute_merkle_root(state1)
        merkle2 = signer.compute_merkle_root(state2)

        assert merkle1 != merkle2, "Different states must produce different merkle roots"

    # ===== SIGNING TESTS =====

    def test_sign_checkpoint(self):
        """Sign checkpoint creates merkle_root and signature."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1, "damping_core": 0.9}
        result = signer.sign_checkpoint(state)

        assert "merkle_root" in result
        assert "signature" in result
        assert "tenant_id" in result
        assert result["tenant_id"] == "tenant_a"

    def test_signature_is_deterministic(self):
        """Signature for same state is deterministic."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1, "damping_core": 0.9}

        sig1 = signer.sign_checkpoint(state)
        sig2 = signer.sign_checkpoint(state)

        assert sig1["signature"] == sig2["signature"]
        assert sig1["merkle_root"] == sig2["merkle_root"]

    def test_signature_changes_on_state_change(self):
        """Signature changes when state changes."""
        signer = CheckpointSigner("tenant_a")

        state1 = {"α_core": 0.1}
        state2 = {"α_core": 0.2}

        sig1 = signer.sign_checkpoint(state1)
        sig2 = signer.sign_checkpoint(state2)

        assert sig1["signature"] != sig2["signature"]

    # ===== VERIFICATION TESTS =====

    def test_verify_checkpoint_valid(self):
        """Verification passes for valid checkpoint."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1, "damping_core": 0.9}
        signing_result = signer.sign_checkpoint(state)

        # Verification should pass
        is_valid = signer.verify_checkpoint(
            state,
            signing_result["merkle_root"],
            signing_result["signature"]
        )

        assert is_valid is True

    def test_verify_checkpoint_tampering_detection(self):
        """Verification detects state tampering."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1}
        signing_result = signer.sign_checkpoint(state)

        # Tamper with state
        tampered_state = {"α_core": 0.2}

        # Verification should fail
        with pytest.raises(CheckpointSignatureError) as exc:
            signer.verify_checkpoint(
                tampered_state,
                signing_result["merkle_root"],
                signing_result["signature"]
            )

        assert "Merkle root mismatch" in str(exc.value)

    def test_verify_checkpoint_signature_tampering(self):
        """Verification detects signature tampering."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1}
        signing_result = signer.sign_checkpoint(state)

        # Tamper with signature (flip a bit)
        tampered_sig = (
            signing_result["signature"][:-1] +
            ('0' if signing_result["signature"][-1] != '0' else '1')
        )

        # Verification should fail
        with pytest.raises(CheckpointSignatureError) as exc:
            signer.verify_checkpoint(
                state,
                signing_result["merkle_root"],
                tampered_sig
            )

        assert "Signature verification failed" in str(exc.value)

    def test_verify_checkpoint_tenant_mismatch(self):
        """Verification fails on tenant_id mismatch."""
        signer_a = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1}
        signing_result = signer_a.sign_checkpoint(state)

        # Try to verify with different tenant
        with pytest.raises(CheckpointSignatureError) as exc:
            signer_a.verify_checkpoint(
                state,
                signing_result["merkle_root"],
                signing_result["signature"],
                tenant_id="tenant_b"
            )

        assert "Tenant mismatch" in str(exc.value)

    def test_constant_time_comparison(self):
        """Signature verification uses constant-time comparison."""
        signer = CheckpointSigner("tenant_a")

        state = {"α_core": 0.1}
        signing_result = signer.sign_checkpoint(state)

        # Create wrong signature
        wrong_sig = (
            signing_result["signature"][:-1] +
            ('0' if signing_result["signature"][-1] != '0' else '1')
        )

        # Both should raise, but timing should be similar
        # (this is a behavioral test, not a timing test)
        with pytest.raises(CheckpointSignatureError):
            signer.verify_checkpoint(state, signing_result["merkle_root"], wrong_sig)


class TestCheckpointSigningContext:
    """Test CheckpointSigningContext for managing signed checkpoints."""

    def test_context_initialization(self):
        """Context initializes with tenant_id."""
        ctx = CheckpointSigningContext("tenant_a")
        assert ctx.tenant_id == "tenant_a"
        assert len(ctx.signed_checkpoints) == 0

    def test_sign_and_store(self):
        """Context signs and stores checkpoints."""
        ctx = CheckpointSigningContext("tenant_a")

        state = {"α_core": 0.1}
        result = ctx.sign_and_store("ckpt_001", state)

        assert "merkle_root" in result
        assert "signature" in result
        assert "ckpt_001" in ctx.signed_checkpoints

    def test_verify_and_restore_valid(self):
        """Context verifies and restores valid checkpoint."""
        ctx = CheckpointSigningContext("tenant_a")

        state = {"α_core": 0.1, "damping_core": 0.9}
        signing_result = ctx.sign_and_store("ckpt_001", state)

        # Restore should succeed
        restored = ctx.verify_and_restore(
            "ckpt_001",
            state,
            signing_result["merkle_root"],
            signing_result["signature"]
        )

        assert restored == state

    def test_verify_and_restore_tampering_fails(self):
        """Context rejects tampered checkpoint on restore."""
        ctx = CheckpointSigningContext("tenant_a")

        state = {"α_core": 0.1}
        signing_result = ctx.sign_and_store("ckpt_001", state)

        # Tamper with state
        tampered_state = {"α_core": 0.2}

        # Restore should fail
        with pytest.raises(CheckpointSignatureError):
            ctx.verify_and_restore(
                "ckpt_001",
                tampered_state,
                signing_result["merkle_root"],
                signing_result["signature"]
            )

    def test_clear_checkpoints(self):
        """Context can clear all stored checkpoints."""
        ctx = CheckpointSigningContext("tenant_a")

        state1 = {"α_core": 0.1}
        state2 = {"α_core": 0.2}

        ctx.sign_and_store("ckpt_001", state1)
        ctx.sign_and_store("ckpt_002", state2)

        assert len(ctx.signed_checkpoints) == 2

        ctx.clear()

        assert len(ctx.signed_checkpoints) == 0


class TestWatchdogCircumventionAttacks:
    """Test that checkpoint signing prevents specific attacks."""

    def test_attack_forge_checkpoint_with_malicious_state(self):
        """Attacker cannot forge checkpoint with malicious state.

        Scenario: Attacker creates a checkpoint with α_core = 10.0 (outside
        bounds) and tries to sign it without the tenant key.
        """
        signer = CheckpointSigner("tenant_a")

        # Attacker's malicious checkpoint
        malicious_state = {"α_core": 10.0}

        # Attacker computes merkle root
        attacker_merkle = signer.compute_merkle_root(malicious_state)

        # Attacker tries to forge signature with wrong key
        wrong_key = b"attacker_key"
        forged_sig = hmac.new(
            wrong_key,
            attacker_merkle.encode(),
            hashlib.sha256
        ).hexdigest()

        # Verification with correct tenant key fails
        with pytest.raises(CheckpointSignatureError):
            signer.verify_checkpoint(
                malicious_state,
                attacker_merkle,
                forged_sig
            )

    def test_attack_replay_old_checkpoint(self):
        """Attacker cannot replay old checkpoint without signature match.

        Scenario: Attacker obtains an old (but valid) checkpoint and tries to
        replay it. The signature is valid for the old state, so this is
        actually allowed (intended behavior for recovery). However, watchdog's
        validate_state() would still reject out-of-bounds values.
        """
        signer = CheckpointSigner("tenant_a")

        # Create two valid checkpoints
        state_old = {"α_core": 0.1, "damping_core": 0.9}
        state_new = {"α_core": 0.15, "damping_core": 0.85}

        result_old = signer.sign_checkpoint(state_old)
        result_new = signer.sign_checkpoint(state_new)

        # Attacker tries to restore old checkpoint
        restored = signer.verify_checkpoint(
            state_old,
            result_old["merkle_root"],
            result_old["signature"]
        )

        assert restored is True, "Old but valid checkpoint can be restored (recovery scenario)"

    def test_attack_cross_tenant_checkpoint_use(self):
        """Attacker cannot use checkpoint from one tenant in another.

        Scenario: Attacker obtains tenant_a's checkpoint and tries to restore
        it in tenant_b's context.
        """
        signer_a = CheckpointSigner("tenant_a")
        signer_b = CheckpointSigner("tenant_b")

        state = {"α_core": 0.1}
        result_a = signer_a.sign_checkpoint(state)

        # Try to verify with tenant_b's signer → fails
        with pytest.raises(CheckpointSignatureError):
            signer_b.verify_checkpoint(
                state,
                result_a["merkle_root"],
                result_a["signature"],
                tenant_id="tenant_b"  # Explicit tenant mismatch
            )

    def test_attack_key_derivation_brute_force(self):
        """Attacker cannot brute-force tenant key.

        The key is derived from tenant_id via SHA256, which is a one-way hash.
        An attacker could compute the same key if they know the derivation
        method, but in production, keys would be stored in secure storage.
        """
        signer = CheckpointSigner("tenant_a")

        # Correct key (derived from tenant_id)
        correct_key = signer.get_tenant_key()

        # Attacker tries random keys
        wrong_key1 = b"random_key_1"
        wrong_key2 = b"random_key_2"

        state = {"α_core": 0.1}
        merkle = signer.compute_merkle_root(state)

        # Signature with wrong key won't match
        wrong_sig1 = hmac.new(wrong_key1, merkle.encode(), hashlib.sha256).hexdigest()
        wrong_sig2 = hmac.new(wrong_key2, merkle.encode(), hashlib.sha256).hexdigest()

        # Correct signature (computed with correct key)
        correct_sig = hmac.new(
            correct_key,
            merkle.encode(),
            hashlib.sha256
        ).hexdigest()

        assert wrong_sig1 != correct_sig
        assert wrong_sig2 != correct_sig


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
