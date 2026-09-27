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
    def temp_corvin_home(self, tmp_path, monkeypatch):
        """Temporary CORVIN_HOME for testing (monkeypatched, never leaked)."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
        return tmp_path

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

    # ── scripts/rotate_corvin_keys_gdpr.py is DEFUSED (adversarial review
    # 2026-09-27): its "rotation" was simulated and it hand-wrote private-format
    # records onto the canonical tenant chain, which made verify_chain report
    # the chain as tampered. These tests pin the refusal. ──────────────────

    def test_rotation_refuses_and_writes_nothing(self, temp_corvin_home, rotation_policy_path):
        tenant_id = "_default"
        audit_path = temp_corvin_home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"

        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        with pytest.raises(NotImplementedError, match="defused"):
            rotate_secrets(str(rotation_policy_path), tenant_id=tenant_id)
        assert not audit_path.exists(), "a refused rotation must not touch the audit chain"

    def test_rotation_leaves_existing_chain_verifiable(self, temp_corvin_home, rotation_policy_path):
        """Regression: one run used to break verify_chain on the tenant chain."""
        from core.deployment.audit_sink import _forge, emit, register_events
        from scripts.rotate_corvin_keys_gdpr import rotate_secrets

        register_events({"deployment.test_rotation_guard": {"n"}})
        emit("deployment.test_rotation_guard", {"n": 1})
        se, fp = _forge()
        chain = fp.tenant_audit_chain("_default")
        assert se.verify_chain(chain)[0]

        with pytest.raises(NotImplementedError):
            rotate_secrets(str(rotation_policy_path), tenant_id="_default")
        ok, problems = se.verify_chain(chain)
        assert ok, problems

    def test_rotation_cli_exits_2(self, temp_corvin_home, rotation_policy_path):
        import subprocess
        import sys as _sys

        script = Path(__file__).resolve().parents[2] / "scripts" / "rotate_corvin_keys_gdpr.py"
        proc = subprocess.run([_sys.executable, str(script), str(rotation_policy_path)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 2
        assert "defused" in proc.stderr
        assert "success" not in proc.stdout

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


        # Rotation script should NOT be called
        with patch("subprocess.run") as mock_run:
            result = verify_secret_rotation_policy()

            # Verify rotation was skipped
            assert not mock_run.called, "Rotation script should not be called for fresh secrets"
            assert result is True


    def _old_state(self, home):
        state_file = home / "tenants" / "_default" / "global" / ".secret_rotation_state"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"last_rotation": "2026-06-01T00:00:00Z"}))

    def test_unparseable_rotation_output_fails_closed(self, temp_corvin_home, rotation_policy_path):
        """Regression: unparseable script output used to be 'assumed success'."""
        self._old_state(temp_corvin_home)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
            with pytest.raises(RuntimeError):
                verify_secret_rotation_policy()

    def test_non_success_status_fails_closed(self, temp_corvin_home, rotation_policy_path):
        self._old_state(temp_corvin_home)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps({"status": "partial"}), stderr="")
            with pytest.raises(RuntimeError):
                verify_secret_rotation_policy()

    def test_real_defused_script_makes_a_due_rotation_fail_closed(self, temp_corvin_home, rotation_policy_path):
        self._old_state(temp_corvin_home)
        with pytest.raises(RuntimeError):
            verify_secret_rotation_policy()
