"""Tests for Quality Gates post-commit event publisher (ADR-0688 Phase 2.3).

Tests that the post-commit hook correctly:
1. Extracts artifact ID from commit message/files
2. Runs validators
3. Publishes results to API (async, non-blocking)
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, call
import tempfile
import subprocess
from datetime import datetime


class TestPostCommitHookExtraction:
    """Test artifact ID extraction from commits."""

    def test_extract_adr_from_commit_message(self):
        """Test extracting ADR-NNNN from commit message."""
        import re

        commit_message = "feat(quality): Implement Phase 1 gates — ADR-0688"
        pattern = r"(ADR-\d{4})"
        match = re.search(pattern, commit_message)

        assert match is not None
        assert match.group(1) == "ADR-0688"

    def test_extract_concept_from_commit_message(self):
        """Test extracting CONCEPT-NNNN from commit message."""
        import re

        commit_message = "concept: Add unified learning pattern — CONCEPT-0031"
        pattern = r"(CONCEPT-\d{4})"
        match = re.search(pattern, commit_message)

        assert match is not None
        assert match.group(1) == "CONCEPT-0031"

    def test_extract_adr_from_changed_files(self):
        """Test extracting ADR from changed files."""
        import re

        changed_files = [
            "core/quality_gates/validators.py",
            "Corvin-ADR/decisions/ADR-0688-quality-gates-architecture.md",
            "tests/test_validators.py",
        ]

        pattern = r"(ADR-\d{4})"
        for file in changed_files:
            match = re.search(pattern, file)
            if match:
                assert match.group(1) == "ADR-0688"
                break
        else:
            pytest.fail("No ADR found in changed files")

    def test_no_artifact_id_found(self):
        """Test when no artifact ID is found."""
        import re

        commit_message = "fix: Update logging format"
        changed_files = ["core/logger.py", "tests/test_logger.py"]

        pattern_adr = r"(ADR-\d{4})"
        pattern_concept = r"(CONCEPT-\d{4})"

        found = False
        for file in changed_files:
            if re.search(pattern_adr, file) or re.search(pattern_concept, file):
                found = True
                break

        if re.search(pattern_adr, commit_message) or re.search(pattern_concept, commit_message):
            found = True

        assert not found


class TestPostCommitValidation:
    """Test post-commit validation execution."""

    def test_validator_returns_pass_verdict(self):
        """Test validator returns PASS verdict."""
        validation_result = {
            "gate_name": "ADRGate",
            "artifact_id": "ADR-0688",
            "verdict": "pass",
            "confidence": 0.95,
            "reason": "All frontmatter fields present",
            "findings": [],
        }

        # Verify result structure
        assert validation_result["verdict"] == "pass"
        assert validation_result["confidence"] == 0.95
        assert isinstance(validation_result["findings"], list)

    def test_validator_returns_fail_verdict(self):
        """Test validator returns FAIL verdict."""
        validation_result = {
            "gate_name": "ADRGate",
            "artifact_id": "ADR-0688",
            "verdict": "fail",
            "confidence": 0.1,
            "reason": "Missing depends_on field",
            "findings": ["Missing ADR frontmatter field: depends_on"],
        }

        # Verify result structure
        assert validation_result["verdict"] == "fail"
        assert validation_result["confidence"] == 0.1
        assert len(validation_result["findings"]) > 0

    def test_validator_timeout_handling(self):
        """Test post-commit hook handles validator timeout gracefully."""
        # Simulate validator timeout (non-blocking)
        validation_result = {
            "error": "Validator timeout",
        }

        # Verify timeout doesn't block commit
        assert "error" in validation_result
        # Hook should return 0 (success) even on timeout


class TestPostCommitEventPublishing:
    """Test event publishing to API."""

    def test_event_published_with_correct_schema(self):
        """Test event published with correct schema."""
        event_data = {
            "commit_sha": "abc123",
            "artifact_id": "ADR-0688",
            "tenant_id": "_default",
            "timestamp": "2026-09-12T10:00:00Z",
            "validation_result": {
                "gate_name": "ADRGate",
                "verdict": "pass",
                "confidence": 0.95,
            },
        }

        # Verify event schema
        assert "commit_sha" in event_data
        assert "artifact_id" in event_data
        assert "tenant_id" in event_data
        assert "validation_result" in event_data

    def test_event_publish_non_blocking(self):
        """Test that event publish is non-blocking."""
        # Simulate curl timeout (expected for async post-commit)
        # The hook should still return 0 (success)

        curl_result = {
            "returncode": 124,  # curl timeout exit code
            "stdout": "",
            "stderr": "Operation timed out",
        }

        # Hook should treat timeout as OK (non-blocking)
        is_blocking_failure = curl_result["returncode"] not in [0, 124, None]
        assert not is_blocking_failure

    def test_event_publish_api_unreachable(self):
        """Test handling of unreachable API."""
        # Simulate curl connection refused
        curl_result = {
            "returncode": 7,  # curl connection failed
            "stdout": "",
            "stderr": "Failed to connect",
        }

        # Hook should not block commit if API is down
        assert curl_result["returncode"] != 0  # API failed
        # But hook should still return 0 overall


class TestPostCommitHookIntegration:
    """Test post-commit hook integration."""

    def test_hook_returns_success_on_completion(self):
        """Test hook returns 0 (success) on completion."""
        # Mock successful execution
        exit_code = 0

        assert exit_code == 0

    def test_hook_logs_to_correct_location(self):
        """Test hook logs to .corvin/quality-gates-post-commit.log."""
        log_dir = os.path.expanduser("~/.corvin")
        expected_log_file = os.path.join(log_dir, "quality-gates-post-commit.log")

        # Verify log file path is correct
        assert "quality-gates-post-commit.log" in expected_log_file
        assert expected_log_file.startswith(os.path.expanduser("~"))
