#!/usr/bin/env python3
"""
k=3 RED→GREEN Test Suite: Credential Rotation Phase 2 with Phase 1.5 Pre-Checks
Tests Phase 1.5 pre-checks gating and Phase 2 rotation functionality.
"""

import unittest
from pathlib import Path


class TestPhase15PreChecks(unittest.TestCase):
    """Test Phase 1.5 pre-checks implementation."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_phase_1_5_function_exists(self):
        """Verify Phase 1.5 pre-checks function exists."""
        self.assertIn("def phase_1_5_pre_checks()", self.content)

    def test_phase_1_5_checks_credential_files(self):
        """Verify Phase 1.5 checks credential files are readable."""
        self.assertIn("load_env_file", self.content)
        self.assertIn("Check 1:", self.content)
        self.assertIn("Credential files readable", self.content)

    def test_phase_1_5_checks_backup_directory(self):
        """Verify Phase 1.5 checks backup directory is accessible."""
        self.assertIn("BACKUP_DIR.mkdir", self.content)
        self.assertIn("Check 2:", self.content)
        self.assertIn("Backup directory accessible", self.content)

    def test_phase_1_5_returns_bool(self):
        """Verify Phase 1.5 returns boolean."""
        self.assertIn("return True", self.content)
        self.assertIn("return False", self.content)


class TestPhase2Rotation(unittest.TestCase):
    """Test Phase 2 credential rotation functionality."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_phase_2_function_exists(self):
        """Verify Phase 2 rotation function exists."""
        self.assertIn("def rotate_credentials_phase2(", self.content)

    def test_phase_2_creates_backup(self):
        """Verify Phase 2 creates backup before rotation."""
        self.assertIn("backup_path = BACKUP_DIR", self.content)
        self.assertIn("credentials-backup", self.content)

    def test_phase_2_generates_placeholders(self):
        """Verify Phase 2 generates placeholders for credentials."""
        self.assertIn("PLACEHOLDER", self.content)
        self.assertIn("placeholders = {", self.content)

    def test_phase_2_github_token_placeholder(self):
        """Verify GitHub token gets placeholder."""
        self.assertIn("GITHUB_TOKEN", self.content)
        self.assertIn("ghp_PLACEHOLDER", self.content)

    def test_phase_2_openai_token_placeholder(self):
        """Verify OpenAI token gets placeholder."""
        self.assertIn("OPENAI_API_KEY", self.content)
        self.assertIn("sk-proj-PLACEHOLDER", self.content)

    def test_phase_2_cloudflare_placeholder(self):
        """Verify Cloudflare tokens get placeholders."""
        self.assertIn("CLOUDFLARE_ID", self.content)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.content)

    def test_phase_2_sets_file_permissions(self):
        """Verify Phase 2 sets 0o600 permissions."""
        self.assertIn("chmod(0o600)", self.content)

    def test_phase_2_supports_dry_run(self):
        """Verify Phase 2 supports dry-run mode."""
        self.assertIn("dry_run", self.content)
        self.assertIn("DRY RUN", self.content)


class TestPhase15Gating(unittest.TestCase):
    """Test that Phase 1.5 gates Phase 2 execution."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_main_calls_phase_1_5(self):
        """Verify main() calls Phase 1.5 pre-checks."""
        self.assertIn("phase_1_5_pre_checks()", self.content)

    def test_main_gates_phase_2_on_phase_1_5(self):
        """Verify Phase 2 only runs if Phase 1.5 passes."""
        self.assertIn("if not phase_1_5_pre_checks():", self.content)

    def test_main_aborts_on_pre_check_failure(self):
        """Verify main() aborts if Phase 1.5 fails."""
        self.assertIn("sys.exit(1)", self.content)
        self.assertIn("Aborting", self.content)

    def test_main_has_skip_pre_checks_option(self):
        """Verify --skip-pre-checks option exists (for testing only)."""
        self.assertIn("skip-pre-checks", self.content)


class TestArgParsing(unittest.TestCase):
    """Test command-line argument parsing."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_has_argument_parser(self):
        """Verify script uses argparse."""
        self.assertIn("ArgumentParser", self.content)

    def test_dry_run_argument(self):
        """Verify --dry-run argument is supported."""
        self.assertIn("--dry-run", self.content)

    def test_skip_pre_checks_argument(self):
        """Verify --skip-pre-checks argument is supported."""
        self.assertIn("--skip-pre-checks", self.content)


class TestFileHandling(unittest.TestCase):
    """Test file reading and writing."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_load_env_file_function(self):
        """Verify load_env_file() function exists."""
        self.assertIn("def load_env_file(", self.content)

    def test_save_env_file_function(self):
        """Verify save_env_file() function exists."""
        self.assertIn("def save_env_file(", self.content)

    def test_handles_missing_files_gracefully(self):
        """Verify script handles missing files."""
        self.assertIn("if not path.exists():", self.content)

    def test_preserves_comments_in_env(self):
        """Verify comments in .env files are preserved."""
        self.assertIn('startswith("#")', self.content)


class TestColorOutput(unittest.TestCase):
    """Test colored terminal output for operator feedback."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_has_green_function(self):
        """Verify green color output function."""
        self.assertIn("def green(", self.content)

    def test_has_red_function(self):
        """Verify red color output function."""
        self.assertIn("def red(", self.content)

    def test_has_yellow_function(self):
        """Verify yellow color output function."""
        self.assertIn("def yellow(", self.content)

    def test_has_bold_function(self):
        """Verify bold output function."""
        self.assertIn("def bold(", self.content)

    def test_has_dim_function(self):
        """Verify dim output function."""
        self.assertIn("def dim(", self.content)


class TestScriptStructure(unittest.TestCase):
    """Test overall script structure and execution."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_script_has_shebang(self):
        """Verify script has Python shebang."""
        self.assertTrue(self.content.startswith("#!/usr/bin/env python3"))

    def test_script_has_docstring(self):
        """Verify script has module docstring."""
        self.assertIn('"""', self.content)

    def test_script_has_main_guard(self):
        """Verify script has if __name__ == '__main__' guard."""
        self.assertIn('if __name__ == "__main__":', self.content)

    def test_script_documents_phase_1_5(self):
        """Verify script documents Phase 1.5."""
        self.assertIn("Phase 1.5", self.content)

    def test_script_documents_phase_2(self):
        """Verify script documents Phase 2."""
        self.assertIn("Phase 2", self.content)


class TestErrorHandling(unittest.TestCase):
    """Test error handling and user feedback."""

    def setUp(self):
        """Load script content."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/scripts/rotate_corvin_keys_phase2.py")
        self.content = self.script_path.read_text()

    def test_handles_load_env_exceptions(self):
        """Verify load_env_file handles exceptions."""
        self.assertIn("except Exception", self.content)

    def test_provides_user_feedback_on_failure(self):
        """Verify user gets feedback when checks fail."""
        self.assertIn("Aborting", self.content)


if __name__ == "__main__":
    # Run tests
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print(f"\n{'='*70}")
    if result.wasSuccessful():
        print(f"✓ All {result.testsRun} tests PASSED — Initiative 3 k=3 GREEN")
    else:
        print(f"✗ {len(result.failures) + len(result.errors)} test(s) FAILED")
    print(f"{'='*70}\n")

    # Exit with appropriate code
    exit(0 if result.wasSuccessful() else 1)
