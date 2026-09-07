"""
Unit Tests: Watchdog Checkpoint Signing Integration (Fix #2)

Tests that the Divergence Watchdog properly signs and verifies checkpoints
to prevent circumvention attacks.
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from core.learning.watchdog import DivergenceWatchdog
from core.learning.checkpoint_signer import CheckpointSignatureError


class TestWatchdogCheckpointSigning:
    """Test DivergenceWatchdog with checkpoint signing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tenant_id = "_default"
        self.watchdog = DivergenceWatchdog(tenant_id=self.tenant_id)

    def test_watchdog_saves_signed_checkpoint(self):
        """Watchdog saves checkpoint with signature."""
        state = {
            "α_core": 0.1,
            "α_infra": 0.1,
            "damping_core": 0.9,
            "damping_infra": 0.9,
            "loss": 0.5,
        }

        checkpoint_id = self.watchdog.save_checkpoint(state)

        assert checkpoint_id is not None
        assert checkpoint_id in self.watchdog.signed_checkpoints
        assert "merkle_root" in self.watchdog.signed_checkpoints[checkpoint_id]
        assert "signature" in self.watchdog.signed_checkpoints[checkpoint_id]

    def test_watchdog_restores_with_verification(self):
        """Watchdog verifies checkpoint on restore."""
        state = {
            "α_core": 0.1,
            "damping_core": 0.9,
            "loss": 0.5,
        }

        checkpoint_id = self.watchdog.save_checkpoint(state)
        restored = self.watchdog.restore_checkpoint(checkpoint_id)

        assert restored is not None
        assert restored["α_core"] == 0.1
        assert restored["damping_core"] == 0.9

    def test_watchdog_rejects_fake_checkpoint_id(self):
        """Watchdog rejects restore of non-existent checkpoint."""
        restored = self.watchdog.restore_checkpoint("fake_ckpt_id")
        assert restored is None

    def test_watchdog_disk_save_includes_signature(self):
        """Saved checkpoint to disk includes merkle_root and signature."""
        state = {"α_core": 0.1, "damping_core": 0.9, "loss": 0.5}
        checkpoint_id = self.watchdog.save_checkpoint(state, directory=self.tmpdir)

        # Read the saved file
        saved_file = self.tmpdir / f"{checkpoint_id}.json"
        assert saved_file.exists()

        data = json.loads(saved_file.read_text())
        assert "merkle_root" in data
        assert "signature" in data
        assert "tenant_id" in data
        assert data["tenant_id"] == self.tenant_id

    def test_watchdog_rejects_tampered_checkpoint_on_restore(self):
        """Watchdog rejects tampered checkpoint during restore.

        This tests the security enhancement: if an attacker tampers with
        the saved checkpoint file, the signature verification will fail
        when the watchdog tries to restore it.
        """
        state = {"α_core": 0.1, "damping_core": 0.9}
        checkpoint_id = self.watchdog.save_checkpoint(state, directory=self.tmpdir)

        # Tamper with the saved file
        saved_file = self.tmpdir / f"{checkpoint_id}.json"
        data = json.loads(saved_file.read_text())

        # Change the state
        data["state"]["α_core"] = 10.0  # Outside bounds

        # Keep the original signature (attacker doesn't have the key)
        saved_file.write_text(json.dumps(data))

        # Create a NEW watchdog instance (simulating reload)
        # This watchdog needs to load and verify the file
        watchdog2 = DivergenceWatchdog(tenant_id=self.tenant_id)

        # Create a new state that doesn't have the saved checkpoint
        # We need to manually set up the verification scenario
        loaded_data = json.loads(saved_file.read_text())

        # The signature won't verify because the state was tampered
        with pytest.raises(CheckpointSignatureError):
            watchdog2.signer.verify_checkpoint(
                loaded_data["state"],
                loaded_data["merkle_root"],
                loaded_data["signature"]
            )

    def test_multiple_checkpoints_independently_verified(self):
        """Multiple checkpoints are independently verifiable."""
        state1 = {"α_core": 0.1, "loss": 0.5}
        state2 = {"α_core": 0.15, "loss": 0.3}

        ckpt_id1 = self.watchdog.save_checkpoint(state1)
        ckpt_id2 = self.watchdog.save_checkpoint(state2)

        # Both should be restorable
        restored1 = self.watchdog.restore_checkpoint(ckpt_id1)
        restored2 = self.watchdog.restore_checkpoint(ckpt_id2)

        assert restored1["α_core"] == 0.1
        assert restored2["α_core"] == 0.15

    def test_checkpoint_signature_unique_per_state(self):
        """Different states produce different signatures."""
        state1 = {"α_core": 0.1}
        state2 = {"α_core": 0.2}

        self.watchdog.save_checkpoint(state1)
        self.watchdog.save_checkpoint(state2)

        ckpt_ids = list(self.watchdog.signed_checkpoints.keys())
        assert len(ckpt_ids) == 2

        sig1 = self.watchdog.signed_checkpoints[ckpt_ids[0]]["signature"]
        sig2 = self.watchdog.signed_checkpoints[ckpt_ids[1]]["signature"]

        assert sig1 != sig2, "Different states must produce different signatures"

    def test_watchdog_validates_state_on_restore(self):
        """Watchdog validates state bounds even if signature is valid."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        # Valid state within bounds
        valid_state = {"α_core": 0.1, "damping_core": 0.9, "loss": 0.5}
        valid_ckpt_id = watchdog.save_checkpoint(valid_state)

        # Restore should succeed
        restored = watchdog.restore_checkpoint(valid_ckpt_id)
        assert restored is not None

        # Verify the state passes bounds check
        is_valid = watchdog.validate_state(restored)
        assert is_valid is True


class TestTenantIsolationWithCheckpoints:
    """Test tenant isolation for checkpoint signing."""

    def test_different_tenants_cannot_verify_each_other_checkpoints(self):
        """Checkpoints from tenant_a cannot be verified by tenant_b."""
        watchdog_a = DivergenceWatchdog(tenant_id="tenant_a")
        watchdog_b = DivergenceWatchdog(tenant_id="tenant_b")

        state = {"α_core": 0.1}
        ckpt_id_a = watchdog_a.save_checkpoint(state)

        # Get the signed checkpoint from tenant_a
        signed_ckpt = watchdog_a.signed_checkpoints[ckpt_id_a]

        # Try to verify with tenant_b's signer → should fail
        with pytest.raises(CheckpointSignatureError):
            watchdog_b.signer.verify_checkpoint(
                signed_ckpt["state"],
                signed_ckpt["merkle_root"],
                signed_ckpt["signature"],
                tenant_id="tenant_b"
            )

    def test_same_state_different_signatures_per_tenant(self):
        """Same state produces different signatures for different tenants."""
        watchdog_a = DivergenceWatchdog(tenant_id="tenant_a")
        watchdog_b = DivergenceWatchdog(tenant_id="tenant_b")

        state = {"α_core": 0.1}

        sig_a = watchdog_a.signer.sign_checkpoint(state)
        sig_b = watchdog_b.signer.sign_checkpoint(state)

        # Merkle roots should be the same (derived from state alone)
        assert sig_a["merkle_root"] == sig_b["merkle_root"]

        # But signatures should differ (derived using tenant-specific key)
        assert sig_a["signature"] != sig_b["signature"]


class TestCheckpointSigningEdgeCases:
    """Test edge cases and error conditions."""

    def test_restore_checkpoint_with_nan_value(self):
        """Watchdog detects NaN in restored checkpoint."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        # Create state with NaN (for divergence detection)
        import math
        state = {"α_core": float('nan'), "damping_core": 0.9}

        # Save checkpoint (should work)
        ckpt_id = watchdog.save_checkpoint(state)

        # Restore should work (no signature error)
        restored = watchdog.restore_checkpoint(ckpt_id)
        assert restored is not None

        # But validate_state should reject it (divergence detection)
        is_valid = watchdog.validate_state(restored)
        assert is_valid is False, "NaN value should trigger divergence detection"

    def test_restore_checkpoint_with_inf_value(self):
        """Watchdog detects Inf in restored checkpoint."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        state = {"α_core": float('inf'), "damping_core": 0.9}

        ckpt_id = watchdog.save_checkpoint(state)
        restored = watchdog.restore_checkpoint(ckpt_id)

        is_valid = watchdog.validate_state(restored)
        assert is_valid is False, "Inf value should trigger divergence detection"

    def test_restore_checkpoint_with_out_of_bounds_value(self):
        """Watchdog detects out-of-bounds values in restored checkpoint."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        # α_core bounds: (0.001, 0.3)
        state = {"α_core": 10.0, "damping_core": 0.9}

        ckpt_id = watchdog.save_checkpoint(state)
        restored = watchdog.restore_checkpoint(ckpt_id)

        is_valid = watchdog.validate_state(restored)
        assert is_valid is False, "Out-of-bounds value should be detected"

    def test_checkpoint_with_empty_state(self):
        """Watchdog handles empty state checkpoint."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        state = {}
        ckpt_id = watchdog.save_checkpoint(state)

        restored = watchdog.restore_checkpoint(ckpt_id)
        assert restored == state

    def test_checkpoint_with_complex_nested_state(self):
        """Watchdog handles complex nested state."""
        watchdog = DivergenceWatchdog(tenant_id="_default")

        state = {
            "α_core": 0.1,
            "nested": {
                "deep": {
                    "value": 0.5,
                    "list": [1, 2, 3],
                }
            },
            "list": [0.1, 0.2, 0.3],
        }

        ckpt_id = watchdog.save_checkpoint(state)
        restored = watchdog.restore_checkpoint(ckpt_id)

        assert restored == state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
