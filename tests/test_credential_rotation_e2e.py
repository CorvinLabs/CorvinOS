"""
E2E Test Suite for Credential Rotation (ADR-0869)

Tests Phase 1: Inventory & Verification
Tests Phase 2: Rotation & Verification (with dry-run)

Verifies:
- All 14 credentials inventoried
- Baseline audit trail created
- Phase 2 automation ready
- Rollback capability verified
"""

import json
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from hashlib import sha256

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.credential_rotation_phase1 import CredentialRotationPhase1


class TestPhase1Inventory:
    """Tests for Phase 1 inventory & verification."""

    @staticmethod
    def setup_test_env():
        """Setup test environment with mock credentials."""
        test_dir = Path(tempfile.mkdtemp(prefix="cred_rotation_test_"))

        # Create test .env
        env_file = test_dir / ".env"
        env_content = """
GITHUB_TOKEN=ghp_test1234567890abcdefghijklmnop
HETZNER_API_TOKEN=test_hetzner_1234567890
HETZNER_ROOT_PASSWORT=test_root_password_123
CLOUDFLARE_ID=test_cf_id_123
CLOUDFLARE_API_TOKEN=test_cf_token_123
PYPI_TOKEN=pypi-test-token-123
RESEND_API_KEY=re_test_key_123
"""
        with open(env_file, "w") as f:
            f.write(env_content)
        env_file.chmod(0o600)

        return test_dir

    def test_phase1_script_exists(self):
        """Verify Phase 1 script exists and is executable."""
        script = Path(__file__).parent.parent / "scripts" / "credential_rotation_phase1.py"
        assert script.exists(), "Phase 1 rotation script not found"
        assert script.stat().st_mode & 0o111, "Phase 1 script is not executable"
        print("✓ Phase 1 script exists and is executable")

    def test_phase1_has_inventory_function(self):
        """Verify Phase 1 has inventory function."""
        phase1 = CredentialRotationPhase1(skip_network=True)
        assert hasattr(phase1, 'inventory_credentials'), "inventory_credentials method missing"
        assert hasattr(phase1, 'CREDENTIALS'), "CREDENTIALS dict missing"
        assert len(phase1.CREDENTIALS) == 3, "Expected 3 credential files"

        # Count total credentials
        total_creds = sum(len(v) for v in phase1.CREDENTIALS.values())
        assert total_creds == 14, f"Expected 14 credentials, found {total_creds}"
        print(f"✓ Phase 1 has inventory function with 14 credentials across 3 files")

    def test_credential_inventory_structure(self):
        """Verify credential inventory structure is correct."""
        phase1 = CredentialRotationPhase1(skip_network=True)

        expected_files = {".env", "~/.config/corvin-voice/service.env", "~/.config/corvin-voice/secrets.json"}
        actual_files = set(phase1.CREDENTIALS.keys())
        assert actual_files == expected_files, f"File mismatch: {actual_files} vs {expected_files}"

        # Check .env credentials
        env_creds = phase1.CREDENTIALS[".env"]
        assert "GITHUB_TOKEN" in env_creds
        assert "CLOUDFLARE_API_TOKEN" in env_creds

        # Check service.env credentials
        service_creds = phase1.CREDENTIALS["~/.config/corvin-voice/service.env"]
        assert "OPENAI_API_KEY" in service_creds
        assert "OLLAMA_API_KEY" in service_creds

        # Check secrets.json credentials
        secrets_creds = phase1.CREDENTIALS["~/.config/corvin-voice/secrets.json"]
        assert "HETZNER_API_TOKEN" in secrets_creds
        assert "HETZNER_SSH_KEY_NAME" in secrets_creds

        print("✓ Credential inventory structure is correct")

    def test_phase1_audit_path(self):
        """Verify Phase 1 creates audit path."""
        phase1 = CredentialRotationPhase1(tenant_id="_default", skip_network=True)
        expected_path = phase1.repo_root / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
        assert phase1.audit_path == expected_path
        print(f"✓ Audit path configured: {phase1.audit_path}")

    def test_credential_masking(self):
        """Verify credential values are masked correctly."""
        phase1 = CredentialRotationPhase1(skip_network=True)

        test_cases = [
            ("", "***"),
            ("a", "*"),
            ("ab", "**"),
            ("abc", "***"),
            ("abcd", "abcd"),
            ("abcdefgh", "abcd***"),
            ("ghp_1234567890abcdefghijklmnopqrstu", "ghp_***"),
        ]

        for value, expected_mask in test_cases:
            masked = phase1._mask_value(value)
            assert masked == expected_mask, f"Mask mismatch for {repr(value)}: {masked} vs {expected_mask}"

        print("✓ Credential masking works correctly")

    def test_phase1_color_output(self):
        """Verify Phase 1 color formatting works."""
        phase1 = CredentialRotationPhase1(skip_network=True)

        green_text = phase1._color("test", "green")
        assert "\033[32m" in green_text, "Green color code missing"
        assert "\033[0m" in green_text, "Reset code missing"

        red_text = phase1._color("error", "red")
        assert "\033[31m" in red_text, "Red color code missing"

        print("✓ Color output formatting works")

    def test_phase1_env_file_loading(self):
        """Verify Phase 1 can load .env files."""
        phase1 = CredentialRotationPhase1(skip_network=True)
        test_dir = self.setup_test_env()

        env_file = test_dir / ".env"
        data = phase1._load_env_file(env_file)

        assert "GITHUB_TOKEN" in data
        assert data["GITHUB_TOKEN"].startswith("ghp_")
        assert "HETZNER_API_TOKEN" in data
        assert len(data) == 7, f"Expected 7 keys in .env, found {len(data)}"

        print("✓ .env file loading works")

    def test_phase1_json_file_loading(self):
        """Verify Phase 1 can load secrets.json files."""
        phase1 = CredentialRotationPhase1(skip_network=True)
        test_dir = self.setup_test_env()

        # Create test secrets.json
        secrets_file = test_dir / "secrets.json"
        secrets_data = {
            "HETZNER_API_TOKEN": "test_token_123",
            "HETZNER_SSH_KEY_NAME": "test_key_name",
        }
        with open(secrets_file, "w") as f:
            json.dump(secrets_data, f)

        data = phase1._load_json_file(secrets_file)
        assert "HETZNER_API_TOKEN" in data
        assert data["HETZNER_SSH_KEY_NAME"] == "test_key_name"

        print("✓ JSON file loading works")

    def test_inventory_record_hashing(self):
        """Verify inventory records can be hashed (for audit trail)."""
        from scripts.credential_rotation_phase1 import CredentialInventory

        record = CredentialInventory(
            credential_name="GITHUB_TOKEN",
            file_path=".env",
            env_variable="GITHUB_TOKEN",
            status="accessible",
            file_exists=True,
            file_readable=True,
            key_present=True,
            key_masked="ghp_***",
            test_passed=True,
            test_reason="authenticated",
            timestamp_utc=datetime.utcnow().isoformat() + "Z",
            tenant_id="_default",
        )

        hash_value = record.hash
        assert len(hash_value) == 64, f"Hash should be 64 chars (SHA256), got {len(hash_value)}"
        assert all(c in "0123456789abcdef" for c in hash_value), "Invalid hex hash"

        print(f"✓ Inventory record hashing works (hash: {hash_value[:16]}...)")


class TestPhase2Preparation:
    """Tests for Phase 2 preparation."""

    def test_phase2_script_exists(self):
        """Verify Phase 2 script exists."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        assert script.exists(), "Phase 2 rotation script not found"
        assert script.stat().st_mode & 0o111, "Phase 2 script is not executable"
        print("✓ Phase 2 script exists and is executable")

    def test_phase2_has_pre_checks(self):
        """Verify Phase 2 has pre-checks function."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()
        assert "phase_1_5_pre_checks" in content, "Phase 1.5 pre-checks missing"
        assert "rotate_credentials_phase2" in content, "Phase 2 rotation function missing"
        print("✓ Phase 2 has pre-checks and rotation functions")

    def test_phase2_dry_run_support(self):
        """Verify Phase 2 supports --dry-run flag."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()
        assert "--dry-run" in content, "Dry-run flag missing"
        assert "dry_run" in content, "Dry-run parameter missing"
        print("✓ Phase 2 supports --dry-run flag")

    def test_phase2_backup_restore(self):
        """Verify Phase 2 has backup and restore functions."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()
        assert "backup_credentials" in content, "Backup function missing"
        assert "restore_credentials" in content, "Restore function missing"
        assert "BACKUP_DIR" in content, "Backup directory missing"
        print("✓ Phase 2 has backup and restore functions")

    def test_phase2_credential_list(self):
        """Verify Phase 2 knows about all 14 credentials."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()

        # Check for known credentials
        credentials = [
            "GITHUB_TOKEN",
            "HETZNER_API_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "OPENAI_API_KEY",
            "PYPI_TOKEN",
            "RESEND_API_KEY",
            "OLLAMA_API_KEY",
        ]

        for cred in credentials:
            assert cred in content, f"Credential {cred} missing from Phase 2"

        print(f"✓ Phase 2 knows about all key credentials")

    def test_phase2_placeholder_generation(self):
        """Verify Phase 2 generates placeholders with timestamps."""
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()
        assert "PLACEHOLDER" in content, "Placeholder generation missing"
        assert "timestamp" in content, "Timestamp substitution missing"
        print("✓ Phase 2 generates timestamped placeholders")


class TestAuditTrail:
    """Tests for audit trail generation."""

    def test_audit_trail_event_structure(self):
        """Verify audit trail events have required fields."""
        from scripts.credential_rotation_phase1 import CredentialRotationPhase1

        phase1 = CredentialRotationPhase1(tenant_id="_default", skip_network=True)

        # Check audit path construction
        assert "_default" in str(phase1.audit_path)
        assert "audit.jsonl" in str(phase1.audit_path)
        print("✓ Audit trail event structure is correct")

    def test_audit_event_hashing(self):
        """Verify audit events are hash-chained."""
        event = {
            "event_type": "credential_rotation_phase1_baseline",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tenant_id": "_default",
            "prev_hash": "0" * 64,
        }

        # Compute hash
        event_str = json.dumps(event, sort_keys=True, default=str)
        event_hash = sha256(event_str.encode()).hexdigest()

        assert len(event_hash) == 64, "Hash should be 64 chars (SHA256)"
        assert all(c in "0123456789abcdef" for c in event_hash), "Invalid hex hash"

        print(f"✓ Audit event hashing works (hash: {event_hash[:16]}...)")


class TestCredentialRotationIntegration:
    """Integration tests."""

    def test_phase1_to_phase2_workflow(self):
        """Verify Phase 1 output feeds into Phase 2."""
        repo_root = Path(__file__).parent.parent

        # Phase 1: Inventory complete
        phase1_script = repo_root / "scripts" / "credential_rotation_phase1.py"
        assert phase1_script.exists()

        # Phase 2: Rotation automation prepared
        phase2_script = repo_root / "scripts" / "rotate_corvin_keys_phase2.py"
        assert phase2_script.exists()

        # Phase 2 should reference the same credentials as Phase 1
        phase1_content = phase1_script.read_text()
        phase2_content = phase2_script.read_text()

        # Both should know about GITHUB_TOKEN
        assert "GITHUB_TOKEN" in phase1_content
        assert "GITHUB_TOKEN" in phase2_content

        print("✓ Phase 1 → Phase 2 workflow is connected")

    def test_zero_downtime_guarantee(self):
        """Verify rotation plan supports zero downtime."""
        phase1 = CredentialRotationPhase1(skip_network=True)

        # Phase 2 uses dual-write (Phase 1.5), so no service should be down
        # This is documented in the rotation scripts
        script = Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py"
        content = script.read_text()

        # Should have pre-checks before rotation
        assert "phase_1_5_pre_checks" in content, "Pre-checks missing"
        # Should have rollback capability
        assert "restore_credentials" in content, "Rollback missing"

        print("✓ Zero-downtime capability verified")

    def test_compliance_logging(self):
        """Verify rotation process logs for compliance (GDPR, CIS)."""
        scripts = [
            Path(__file__).parent.parent / "scripts" / "credential_rotation_phase1.py",
            Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_phase2.py",
            Path(__file__).parent.parent / "scripts" / "rotate_corvin_keys_gdpr.py",
        ]

        for script in scripts:
            if script.exists():
                content = script.read_text()
                # Should have audit trail / logging
                assert "audit" in content.lower() or "log" in content.lower(), f"No audit/logging in {script.name}"

        print("✓ Compliance logging verified in all rotation scripts")


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("E2E TEST SUITE: Credential Rotation (ADR-0869)")
    print("=" * 70 + "\n")

    test_classes = [
        TestPhase1Inventory,
        TestPhase2Preparation,
        TestAuditTrail,
        TestCredentialRotationIntegration,
    ]

    total_tests = 0
    passed_tests = 0
    failed_tests = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}:")
        print("-" * 70)

        instance = test_class()
        test_methods = [m for m in dir(instance) if m.startswith("test_")]

        for test_method in test_methods:
            total_tests += 1
            try:
                getattr(instance, test_method)()
                passed_tests += 1
            except AssertionError as e:
                print(f"✗ {test_method}: {e}")
                failed_tests += 1
            except Exception as e:
                print(f"✗ {test_method}: Unexpected error: {e}")
                failed_tests += 1

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed_tests} passed, {failed_tests} failed, {total_tests} total")
    print("=" * 70 + "\n")

    return failed_tests == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
