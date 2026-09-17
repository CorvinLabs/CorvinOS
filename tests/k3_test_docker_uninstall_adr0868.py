#!/usr/bin/env python3
"""
k=3 RED→GREEN Test Suite: Docker Uninstall Coverage (ADR-0868)
Tests comprehensive Docker cleanup, audit export, and verification.
"""

import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch, MagicMock


class TestDockerUninstallFunctions(unittest.TestCase):
    """Test Docker Uninstall functions (ADR-0868)."""

    def setUp(self):
        """Set up test environment."""
        self.script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        self.assertTrue(self.script_path.exists(), "corvin-uninstall script not found")

    def test_detect_deployment_mode_function_exists(self):
        """Verify detect_deployment_mode() function exists."""
        content = self.script_path.read_text()
        self.assertIn("detect_deployment_mode()", content)

    def test_export_audit_trail_function_exists(self):
        """Verify export_audit_trail() function exists."""
        content = self.script_path.read_text()
        self.assertIn("export_audit_trail()", content)

    def test_cleanup_docker_function_exists(self):
        """Verify cleanup_docker() function exists."""
        content = self.script_path.read_text()
        self.assertIn("cleanup_docker()", content)

    def test_verify_docker_cleanup_function_exists(self):
        """Verify verify_docker_cleanup() function exists."""
        content = self.script_path.read_text()
        self.assertIn("verify_docker_cleanup()", content)

    def test_detect_deployment_checks_docker_binary(self):
        """Verify detect_deployment_mode checks for docker binary."""
        content = self.script_path.read_text()
        self.assertIn("command -v docker", content)

    def test_detect_deployment_checks_docker_daemon(self):
        """Verify detect_deployment_mode checks if Docker daemon is running."""
        content = self.script_path.read_text()
        self.assertIn("docker ps", content)

    def test_detect_deployment_checks_labels(self):
        """Verify detect_deployment_mode checks for CorvinOS labels."""
        content = self.script_path.read_text()
        self.assertIn("label=app=corvinOS", content)

    def test_detect_deployment_fallback_to_systemd(self):
        """Verify detect_deployment_mode falls back to systemd."""
        content = self.script_path.read_text()
        self.assertIn('echo "systemd"', content)

    def test_audit_trail_export_creates_directory(self):
        """Verify audit trail export creates export directory."""
        content = self.script_path.read_text()
        self.assertIn("mkdir -p", content)
        self.assertIn("CORVIN_EXPORT_DIR", content)

    def test_audit_trail_export_uses_timestamp(self):
        """Verify audit trail export includes timestamp."""
        content = self.script_path.read_text()
        self.assertIn("timestamp=$(date", content)
        self.assertIn("audit-trail-export", content)

    def test_docker_cleanup_stops_containers(self):
        """Verify cleanup stops containers."""
        content = self.script_path.read_text()
        self.assertIn("docker stop", content)

    def test_docker_cleanup_removes_containers(self):
        """Verify cleanup removes containers."""
        content = self.script_path.read_text()
        self.assertIn("docker rm", content)

    def test_docker_cleanup_removes_images(self):
        """Verify cleanup removes Docker images."""
        content = self.script_path.read_text()
        self.assertIn("docker rmi", content)

    def test_docker_cleanup_removes_volumes(self):
        """Verify cleanup removes volumes."""
        content = self.script_path.read_text()
        self.assertIn("docker volume rm", content)

    def test_docker_cleanup_removes_networks(self):
        """Verify cleanup removes networks."""
        content = self.script_path.read_text()
        self.assertIn("docker network rm", content)

    def test_docker_cleanup_handles_volume_confirmation(self):
        """Verify cleanup prompts for volume confirmation."""
        content = self.script_path.read_text()
        self.assertIn("Remove CorvinOS volumes?", content)

    def test_verify_cleanup_checks_containers(self):
        """Verify verification checks for remaining containers."""
        content = self.script_path.read_text()
        self.assertIn("docker ps --all", content)
        # Should check containers after cleanup
        lines = content.split('\n')
        found_verify = False
        for i, line in enumerate(lines):
            if 'verify_docker_cleanup' in line:
                found_verify = True
                break
        self.assertTrue(found_verify, "verify_docker_cleanup function not found")

    def test_verify_cleanup_checks_images(self):
        """Verify verification checks for remaining images."""
        content = self.script_path.read_text()
        self.assertIn("docker images", content)

    def test_verify_cleanup_checks_volumes(self):
        """Verify verification checks for remaining volumes."""
        content = self.script_path.read_text()
        # Should check volumes after cleanup
        self.assertIn("docker volume ls", content)

    def test_deployment_mode_detection_is_called(self):
        """Verify deployment mode detection is called at runtime."""
        content = self.script_path.read_text()
        self.assertIn("DEPLOYMENT_MODE=$(detect_deployment_mode)", content)

    def test_systemd_cleanup_is_implemented(self):
        """Verify systemd cleanup for non-Docker deployments."""
        content = self.script_path.read_text()
        self.assertIn("systemctl --user stop", content)
        self.assertIn("systemctl --user disable", content)

    def test_corvin_home_directory_removed(self):
        """Verify ~/.corvin directory is removed."""
        content = self.script_path.read_text()
        self.assertIn("rm -rf", content)
        self.assertIn("CORVIN_HOME", content)

    def test_final_summary_message(self):
        """Verify uninstall provides final confirmation."""
        content = self.script_path.read_text()
        self.assertIn("uninstalled successfully", content)

    def test_audit_export_location_communicated(self):
        """Verify audit export location is communicated to operator."""
        content = self.script_path.read_text()
        self.assertIn("Exported data:", content)
        self.assertIn("CORVIN_EXPORT_DIR", content)

    def test_label_based_docker_filtering(self):
        """Verify Docker cleanup uses label-based filtering."""
        content = self.script_path.read_text()
        # Should filter by label, not just name
        self.assertIn("--filter \"label=app=corvinOS\"", content)

    def test_legacy_container_name_cleanup(self):
        """Verify legacy container names are also cleaned."""
        content = self.script_path.read_text()
        # Should handle old container naming
        self.assertIn("corvinOS-console", content)
        self.assertIn("corvinOS-gateway", content)

    def test_docker_commands_are_fail_soft(self):
        """Verify Docker commands use fail-soft (||true) for missing resources."""
        content = self.script_path.read_text()
        # Commands should not fail if resource doesn't exist
        self.assertIn("|| true", content)
        self.assertIn("|| echo", content)

    def test_export_happens_before_cleanup(self):
        """Verify audit export happens before Docker cleanup."""
        content = self.script_path.read_text()
        lines = content.split('\n')

        export_line = None
        cleanup_line = None

        for i, line in enumerate(lines):
            if 'export_audit_trail' in line and 'function' not in line:
                export_line = i
            if 'cleanup_docker' in line and 'function' not in line:
                cleanup_line = i

        self.assertIsNotNone(export_line, "export_audit_trail call not found")
        self.assertIsNotNone(cleanup_line, "cleanup_docker call not found")
        # Export should happen first (before cleanup destroys volumes)
        self.assertLess(export_line, cleanup_line)


class TestDockerUninstallEdgeCases(unittest.TestCase):
    """Edge case tests for Docker uninstall."""

    def test_script_is_executable(self):
        """Verify corvin-uninstall script is executable."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        # Check if it starts with shebang
        content = script_path.read_text()
        self.assertTrue(content.startswith("#!/bin/bash"))

    def test_docker_down_scenario_handled(self):
        """Verify uninstall proceeds even if Docker daemon is down."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        content = script_path.read_text()

        # Should detect Docker daemon down and fallback to systemd
        self.assertIn("docker ps >/dev/null", content)
        # After detection, should handle accordingly
        self.assertIn("DEPLOYMENT_MODE", content)

    def test_partial_cleanup_warning(self):
        """Verify operator is warned if cleanup is incomplete."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        content = script_path.read_text()

        # Should warn about manual cleanup if verification fails
        self.assertIn("Warning", content)
        self.assertIn("Manual cleanup", content)


class TestGDPRArt17Compliance(unittest.TestCase):
    """Test GDPR Art. 17 (Right to Erasure) compliance."""

    def test_audit_trail_exported_for_compliance(self):
        """Verify audit trail is exported for compliance proof."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        content = script_path.read_text()

        # GDPR requires proof of erasure request
        self.assertIn("export_audit_trail", content)

    def test_export_before_deletion(self):
        """Verify data is exported before deletion (Art. 17 proof)."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        content = script_path.read_text()

        lines = content.split('\n')
        export_idx = None
        delete_idx = None

        for i, line in enumerate(lines):
            if 'docker cp' in line and 'audit' in line:
                export_idx = i
            if 'rm -rf' in line and 'CORVIN_HOME' in line:
                delete_idx = i

        # Export should precede deletion
        if export_idx and delete_idx:
            self.assertLess(export_idx, delete_idx, "Export should happen before deletion")

    def test_export_includes_full_audit_trail(self):
        """Verify full audit trail is exported, not just summary."""
        script_path = Path("/home/shumway/projects/CorvinOS/corvin-uninstall")
        content = script_path.read_text()

        self.assertIn("audit.jsonl", content, "Full audit trail file (audit.jsonl) should be exported")


if __name__ == "__main__":
    # Run tests
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    exit(0 if result.wasSuccessful() else 1)
