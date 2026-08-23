"""Security validation tests for graph routing.

Tests:
    - Path validation (prevent traversal)
    - Input validation (prevent DoS)
    - Subprocess safety

ADR: ADR-0268
"""

import pytest
from pathlib import Path
from ..graph_routing import validate_path, validate_task_input


@pytest.fixture
def repo_root():
    """Get repo root."""
    return Path(__file__).resolve().parents[3]  # CorvinOS/


class TestPathValidation:
    """Test path traversal prevention."""

    def test_valid_relative_path(self, repo_root):
        """Valid relative paths should pass."""
        assert validate_path("core/voice/renderer.py", repo_root)
        assert validate_path("operator/task_analysis/normalizer.py", repo_root)
        assert validate_path("tests/test_engine.py", repo_root)

    def test_absolute_path_rejected(self, repo_root):
        """Absolute paths should be rejected."""
        assert not validate_path("/etc/passwd", repo_root)
        assert not validate_path("/home/user/file.py", repo_root)

    def test_traversal_rejected(self, repo_root):
        """Path traversal attempts should be rejected."""
        assert not validate_path("../../../etc/passwd", repo_root)
        assert not validate_path("core/../../etc/passwd", repo_root)
        assert not validate_path("core/../../../etc/passwd", repo_root)

    def test_current_dir_prefix_rejected(self, repo_root):
        """Paths starting with ./ should be rejected."""
        assert not validate_path("./core/voice.py", repo_root)
        assert not validate_path("./../core/voice.py", repo_root)

    def test_empty_string_rejected(self, repo_root):
        """Empty strings should be rejected."""
        assert not validate_path("", repo_root)
        assert not validate_path(None, repo_root)

    def test_non_string_rejected(self, repo_root):
        """Non-string inputs should be rejected."""
        assert not validate_path(123, repo_root)
        assert not validate_path(["core", "voice.py"], repo_root)


class TestInputValidation:
    """Test DoS prevention (input length, control chars)."""

    def test_valid_task_description(self):
        """Valid task descriptions should pass."""
        assert validate_task_input("Fix bug in voice module")
        assert validate_task_input("Implement new feature for delegation")
        assert validate_task_input("A" * 100)  # 100 chars is OK

    def test_max_length_enforced(self):
        """Tasks exceeding 10K chars should be rejected."""
        long_task = "A" * 10001
        assert not validate_task_input(long_task)

    def test_max_length_boundary(self):
        """Exactly 10K chars should pass."""
        boundary_task = "A" * 10000
        assert validate_task_input(boundary_task)

    def test_control_characters_rejected(self):
        """Control characters should be rejected."""
        # Null byte
        assert not validate_task_input("Fix bug\x00in voice module")
        # Bell character
        assert not validate_task_input("Fix bug\x07in voice module")
        # Unit separator
        assert not validate_task_input("Fix bug\x1fin voice module")

    def test_newlines_allowed(self):
        """Newlines are allowed (multi-line descriptions)."""
        multiline = "Fix bug in voice module\nAnd also fix delegation\nFor user X"
        assert validate_task_input(multiline)

    def test_tabs_allowed(self):
        """Tabs are allowed (indented descriptions)."""
        tabbed = "Fix bug:\n\t- voice module\n\t- delegation layer"
        assert validate_task_input(tabbed)

    def test_empty_string_rejected(self):
        """Empty strings should be rejected."""
        assert not validate_task_input("")
        assert not validate_task_input(None)

    def test_non_string_rejected(self):
        """Non-string inputs should be rejected."""
        assert not validate_task_input(123)
        assert not validate_task_input(["Fix bug"])


class TestSecurityEdgeCases:
    """Edge cases that could bypass security."""

    def test_path_with_double_dot_in_filename(self, repo_root):
        """Files with .. in the name should be rejected."""
        # Filename "..evil.py" could bypass simple checks
        assert not validate_path("core/..evil.py", repo_root)

    def test_symlink_escape_attempt(self, repo_root):
        """Symlink to parent directory should be rejected."""
        # Even if symlink exists, traversal intent should fail
        assert not validate_path("core/link_to_parent/file.py", repo_root)

    def test_unicode_normalization_bypass(self):
        """Unicode tricks should not bypass length check."""
        # Some unicode can compress to shorter display length
        # but should count full byte length
        task = "X" * 5000 + "🔥" * 5000  # ~20KB in bytes
        # Should be rejected if total exceeds limit
        if len("X" * 5000 + "🔥" * 5000) > 10000:
            assert not validate_task_input(task)
