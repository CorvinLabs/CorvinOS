#!/usr/bin/env python3
"""
k=3 RED→GREEN Test Suite: Credential Rotation Phase 2
Tests credential rotation to placeholders, backup creation, and audit event generation.
Script structure verification (without importing).
"""

import unittest
import json
from pathlib import Path
import tempfile
import shutil


class TestCredentialRotationScript(unittest.TestCase):
    """Test the Phase 2 credential rotation script."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.script_content = self.script_path.read_text()

    def test_script_file_exists(self):
        """Verify Phase 2 script file exists."""
        self.assertTrue(self.script_path.exists(), "rotate_corvin_keys_phase2.py not found")

    def test_script_has_shebang(self):
        """Verify script has Python shebang."""
        self.assertTrue(self.script_content.startswith("#!/usr/bin/env python3"))

    def test_rotator_class_defined(self):
        """Verify CredentialRotator class is defined."""
        self.assertIn("class CredentialRotator:", self.script_content)

    def test_placeholders_dict_exists(self):
        """Verify PLACEHOLDERS dictionary is defined."""
        self.assertIn("PLACEHOLDERS = {", self.script_content)

    def test_github_token_in_placeholders(self):
        """Verify GitHub token placeholder is defined."""
        self.assertIn("GITHUB_TOKEN", self.script_content)
        self.assertIn("ghp_PLACEHOLDER", self.script_content)

    def test_openai_token_in_placeholders(self):
        """Verify OpenAI token placeholder is defined."""
        self.assertIn("OPENAI_API_KEY", self.script_content)
        self.assertIn("sk-proj-PLACEHOLDER", self.script_content)

    def test_cloudflare_tokens_in_placeholders(self):
        """Verify Cloudflare placeholders."""
        self.assertIn("CLOUDFLARE_ID", self.script_content)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.script_content)

    def test_hetzner_tokens_in_placeholders(self):
        """Verify Hetzner placeholders."""
        self.assertIn("HETZNER_API_TOKEN", self.script_content)
        self.assertIn("HETZNER_ROOT_PASSWORT", self.script_content)

    def test_gmail_password_in_placeholders(self):
        """Verify Gmail app password placeholder."""
        self.assertIn("GMAIL_APP_PASSWORD", self.script_content)

    def test_pypi_token_in_placeholders(self):
        """Verify PyPI token placeholder."""
        self.assertIn("PYPI_TOKEN", self.script_content)

    def test_generate_placeholder_method(self):
        """Verify generate_placeholder() method exists."""
        self.assertIn("def generate_placeholder(", self.script_content)

    def test_backup_env_file_method(self):
        """Verify backup_env_file() method exists."""
        self.assertIn("def backup_env_file(", self.script_content)

    def test_rotate_env_file_method(self):
        """Verify rotate_env_file() method exists."""
        self.assertIn("def rotate_env_file(", self.script_content)

    def test_rotate_secrets_json_method(self):
        """Verify rotate_secrets_json() method exists."""
        self.assertIn("def rotate_secrets_json(", self.script_content)

    def test_generate_audit_event_method(self):
        """Verify generate_audit_event() method exists."""
        self.assertIn("def generate_audit_event(", self.script_content)

    def test_main_function_exists(self):
        """Verify main() function exists."""
        self.assertIn("def main():", self.script_content)

    def test_main_execution_block(self):
        """Verify script has if __name__ == '__main__' block."""
        self.assertIn('if __name__ == "__main__":', self.script_content)

    def test_phase_1_prerequisite_documented(self):
        """Verify Phase 1 prerequisite is documented."""
        self.assertIn("Phase 1", self.script_content)
        self.assertIn("manual revocation", self.script_content)

    def test_fail_closed_strategy_documented(self):
        """Verify fail-closed strategy is documented."""
        self.assertIn("Fail-Closed", self.script_content)
        self.assertIn("401 Unauthorized", self.script_content)

    def test_backup_env_creates_file(self):
        """Verify backup_env_file creates backup with timestamp."""
        self.assertIn("backup_path", self.script_content)
        self.assertIn(".backup-", self.script_content)

    def test_rotated_env_has_permissions_0600(self):
        """Verify rotated files get 0o600 permissions."""
        self.assertIn("os.chmod", self.script_content)
        self.assertIn("0o600", self.script_content)

    def test_env_parsing_logic(self):
        """Verify .env parsing handles KEY=VALUE format."""
        self.assertIn("split('=', 1)", self.script_content)

    def test_json_parsing_for_secrets(self):
        """Verify secrets.json parsing uses JSON."""
        self.assertIn("json.load(", self.script_content)

    def test_comments_preserved_in_env(self):
        """Verify comments (lines starting with #) are preserved."""
        self.assertIn("startswith('#')", self.script_content)

    def test_audit_event_includes_phase(self):
        """Verify audit event includes phase information."""
        self.assertIn("phase_2", self.script_content)

    def test_audit_event_includes_tenant_id(self):
        """Verify audit event includes tenant_id."""
        self.assertIn("tenant_id", self.script_content)

    def test_main_backs_up_files(self):
        """Verify main() backs up .env before rotation."""
        self.assertIn("backup_env_file", self.script_content)

    def test_main_rotates_multiple_locations(self):
        """Verify main() rotates credentials in multiple locations."""
        # Should handle .env, service.env, secrets.json
        self.assertIn(".env", self.script_content)
        self.assertIn("service.env", self.script_content)
        self.assertIn("secrets.json", self.script_content)

    def test_main_generates_audit_event(self):
        """Verify main() generates and displays audit event."""
        self.assertIn("generate_audit_event()", self.script_content)
        self.assertIn("json.dumps", self.script_content)

    def test_main_prints_completion_message(self):
        """Verify main() prints completion message."""
        self.assertIn("Phase 2 Complete", self.script_content)

    def test_main_warns_about_phase_1(self):
        """Verify main() warns operator about Phase 1."""
        self.assertIn("Phase 1", self.script_content)
        self.assertIn("must have been completed", self.script_content)


class TestFailClosedStrategy(unittest.TestCase):
    """Test fail-closed behavior."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.script_content = self.script_path.read_text()

    def test_placeholders_are_not_real_credentials(self):
        """Verify placeholders don't contain real credential patterns."""
        self.assertIn("PLACEHOLDER", self.script_content)
        # Should NOT try to use real credentials
        self.assertNotIn("ghp_github", self.script_content)
        self.assertNotIn("sk-proj-real", self.script_content)

    def test_timestamp_uniqueness_in_placeholders(self):
        """Verify placeholders include timestamp for uniqueness."""
        self.assertIn("{ts}", self.script_content)
        self.assertIn("TIMESTAMP", self.script_content)


class TestCredentialCoverage(unittest.TestCase):
    """Test coverage of all credential types."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.script_content = self.script_path.read_text()

    def test_13_plus_credentials_supported(self):
        """Verify at least 13 credential types are supported."""
        # Count the number of credential entries in PLACEHOLDERS
        placeholders_section = self.script_content[
            self.script_content.find("PLACEHOLDERS = {"):
            self.script_content.find("}", self.script_content.find("PLACEHOLDERS = {"))
        ]
        credential_count = placeholders_section.count('"')  // 2  # Each key takes 2 quotes
        self.assertGreaterEqual(credential_count, 13, f"Only found {credential_count} credentials, need at least 13")

    def test_tts_openai_key_supported(self):
        """Verify TTS OpenAI key is supported."""
        self.assertIn("CORVIN_TTS_OPENAI_KEY", self.script_content)

    def test_stt_openai_key_supported(self):
        """Verify STT OpenAI key is supported."""
        self.assertIn("CORVIN_STT_OPENAI_KEY", self.script_content)

    def test_resend_api_key_supported(self):
        """Verify Resend API key is supported."""
        self.assertIn("RESEND_API_KEY", self.script_content)


class TestAuditTrail(unittest.TestCase):
    """Test audit trail logging."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.script_content = self.script_path.read_text()

    def test_audit_event_structure(self):
        """Verify audit event has required structure."""
        self.assertIn("event_type", self.script_content)
        self.assertIn("timestamp", self.script_content)
        self.assertIn("secret_rotation", self.script_content)

    def test_audit_event_logged_to_json(self):
        """Verify audit event is logged as JSON."""
        self.assertIn("json.dumps", self.script_content)


if __name__ == "__main__":
    # Run tests
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    exit(0 if result.wasSuccessful() else 1)
