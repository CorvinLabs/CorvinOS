"""GDPR Art. 32 Secret Rotation Compliance Tests (ADR-0758)."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import modules
from core.compliance.bootstrap_rotation import verify_secret_rotation_policy


class TestSecretRotationGDPR:
    """Test suite for GDPR Art. 32 secret rotation."""

    @pytest.fixture
    def temp_corvin_home(self):
        """Temporary CORVIN_HOME for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            os.environ["CORVIN_HOME"] = str(corvin_home)
            yield corvin_home

    @pytest.fixture
    def rotation_policy_path(self, temp_corvin_home):
        """Create rotation policy YAML for testing."""
        policy_dir = temp_corvin_home / "tenants" / "_default" / "global" / "compliance"
        policy_dir.mkdir(parents=True, exist_ok=True)
        policy_path = policy_dir / "rotation_policy.yaml"

        policy_content = """
rotation:
  enabled: true
  interval_days: 90
  audit_trail_enabled: true
  secrets:
    - id: api_keys
      count: 3
      type: hmac-sha256
      storage: ~/.corvin/secrets/api_keys.json
      rotation_enabled: true
"""
        with open(policy_path, "w") as f:
            f.write(policy_content)

        return policy_path

    def test_rotation_creates_audit_events(self, temp_corvin_home, rotation_policy_path):
        """Test that rotation creates audit events (GDPR Art. 32)."""
        tenant_id = "_default"
        audit_path = temp_corvin_home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        # Import rotation script
        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        result = rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)

        # Verify result
        assert result["status"] == "success"
        assert result["secrets_rotated"] == 1
        assert result["phases_completed"] == 4

        # Verify audit events were created
        assert audit_path.exists(), "Audit trail not created"

        with open(audit_path) as f:
            events = [json.loads(line) for line in f.readlines()]

        # Should have 4 events (one per phase)
        assert len(events) >= 4, f"Expected ≥4 audit events, got {len(events)}"

        # Verify event types
        event_types = [e["event_type"] for e in events]
        assert "rotation_phase_1_generate" in event_types
        assert "rotation_phase_2_dual_write" in event_types
        assert "rotation_phase_3_revoke" in event_types
        assert "rotation_phase_4_cleanup" in event_types

    def test_hash_chain_integrity(self, temp_corvin_home, rotation_policy_path):
        """Test that audit events form an intact hash chain."""
        tenant_id = "_default"
        audit_path = temp_corvin_home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)

        with open(audit_path) as f:
            events = [json.loads(line) for line in f.readlines()]

        # Verify hash chain: each event's prev_hash should match previous event's hash
        for i in range(1, len(events)):
            current_event = events[i]
            previous_event = events[i - 1]

            # prev_hash should exist and match
            assert current_event.get("prev_hash") == previous_event.get("hash"), \
                f"Event {i}: hash chain broken (prev_hash mismatch)"

    def test_audit_events_include_tenant_id(self, temp_corvin_home, rotation_policy_path):
        """Test that audit events include tenant_id (tenant isolation)."""
        tenant_id = "_default"
        audit_path = temp_corvin_home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)

        with open(audit_path) as f:
            events = [json.loads(line) for line in f.readlines()]

        # All events must include tenant_id
        for event in events:
            assert "tenant_id" in event, f"Event {event['event_type']} missing tenant_id"
            assert event["tenant_id"] == tenant_id, f"Event has wrong tenant_id: {event['tenant_id']}"

    def test_rotation_fails_if_audit_unavailable(self, temp_corvin_home, rotation_policy_path):
        """Test fail-closed: rotation aborts if audit chain is unreachable."""
        tenant_id = "_default"

        # Don't create audit path (simulate unavailable audit backend)
        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        # Mock write permission denial
        with patch("builtins.open", side_effect=PermissionError("Audit write denied")):
            with pytest.raises(RuntimeError, match="Audit chain write failed"):
                rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)

    def test_bootstrap_check_detects_old_secrets(self, temp_corvin_home, rotation_policy_path):
        """Test bootstrap check: detects if secrets >90 days old."""
        tenant_id = "_default"
        state_file = temp_corvin_home / "tenants" / tenant_id / "global" / ".secret_rotation_state"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        # Simulate old rotation state (>90 days ago)
        old_state = {
            "last_rotation": "2026-06-01T00:00:00Z"  # ~90 days before test date 2026-09-17
        }
        with open(state_file, "w") as f:
            json.dump(old_state, f)

        os.environ["CORVIN_TENANT_ID"] = tenant_id

        # Mock the rotation script to avoid actual execution
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"status": "success", "secrets_rotated": 1}),
                stderr="",
            )

            result = verify_secret_rotation_policy()

            # Verify rotation was triggered
            assert mock_run.called, "Rotation script not called for old secrets"
            assert result is True

    def test_bootstrap_skips_if_secrets_fresh(self, temp_corvin_home, rotation_policy_path):
        """Test bootstrap check: skips rotation if secrets are fresh (<90 days old)."""
        tenant_id = "_default"
        state_file = temp_corvin_home / "tenants" / tenant_id / "global" / ".secret_rotation_state"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        # Simulate fresh rotation state (1 day ago)
        fresh_state = {
            "last_rotation": "2026-09-16T00:00:00Z"  # 1 day before test date 2026-09-17
        }
        with open(state_file, "w") as f:
            json.dump(fresh_state, f)

        os.environ["CORVIN_TENANT_ID"] = tenant_id

        # Rotation script should NOT be called
        with patch("subprocess.run") as mock_run:
            result = verify_secret_rotation_policy()

            # Verify rotation was skipped
            assert not mock_run.called, "Rotation script should not be called for fresh secrets"
            assert result is True

    def test_rotation_all_phases_complete(self, temp_corvin_home, rotation_policy_path):
        """Test that all 4 rotation phases complete successfully."""
        tenant_id = "_default"
        audit_path = temp_corvin_home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        result = rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)

        # Verify all phases
        assert result["phases_completed"] == 4

        # Verify phase results are present
        assert "api_keys_phase_1" in result["results"]
        assert "api_keys_phase_2" in result["results"]
        assert "api_keys_phase_3" in result["results"]
        assert "api_keys_phase_4" in result["results"]

        # Verify phase 2 dual-write window is set
        phase_2 = result["results"]["api_keys_phase_2"]
        assert "phase_2_start" in phase_2
        assert "phase_2_end" in phase_2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
