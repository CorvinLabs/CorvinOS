"""Test that llm_synthesis sanitizes contaminated task input (ADR-2101 P2)."""
import pytest
from unittest.mock import Mock
from corvin_operator.context_engineering.stages.llm_synthesis import _sanitize_task_text


class TestSanitizeTaskText:
    """Verify that synthesis temp paths are removed from task input."""

    def test_removes_synthesis_temp_path(self):
        """Removal of ~/.claude/projects/-tmp-corvin-ce-synthesis-*/ paths."""
        task = (
            "Implementiere ADR-0000.\n\n"
            "Constraint: ~/.claude/projects/-tmp-corvin-ce-synthesis-abc123/memory/\n\n"
            "Die Aufgabe ist zu bauen..."
        )
        result = _sanitize_task_text(task)
        assert "-tmp-corvin-ce-synthesis" not in result
        assert "Die Aufgabe" in result

    def test_handles_multiple_paths(self):
        """Multiple temp paths in one task."""
        task = (
            "Start in ~/.claude/projects/-tmp-corvin-ce-synthesis-p1/memory/\n"
            "Then go to ~/.claude/projects/-tmp-corvin-ce-synthesis-p2/\n"
            "Do work"
        )
        result = _sanitize_task_text(task)
        assert "-tmp-corvin-ce-synthesis" not in result
        assert "Do work" in result

    def test_preserves_non_temp_paths(self):
        """Keep valid paths that are not synthesis temps."""
        task = (
            "Read ~/.corvin/audit.log\n"
            "Check /home/shumway/projects/CorvinOS/\n"
            "Work here"
        )
        result = _sanitize_task_text(task)
        assert "~/.corvin" in result
        assert "/home/shumway/projects" in result
        assert "Work" in result

    def test_noop_on_clean_input(self):
        """No change when input is clean."""
        task = "Implementiere ADR-2101 korrekt.\n\nDetails hier."
        result = _sanitize_task_text(task)
        assert result == task

    def test_handles_non_string_input(self):
        """Non-string input returns unchanged."""
        result = _sanitize_task_text(None)
        assert result is None
        result = _sanitize_task_text(12345)
        assert result == 12345
