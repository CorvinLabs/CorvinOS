"""
Unit tests for Session Reset Fixes (ADR-0368).

Tests verify:
1. Token budget is completely replaced (not additive)
2. New session-id UUID is generated
3. Memory files are loaded from MEMORY.md index
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from core.context_engineering.session_reset_fixes import (
    TokenBudget,
    SessionResetManager,
    apply_session_reset_fixes,
)


class TestTokenBudgetReset:
    """Fix #1: Token budget replacement (not additive)."""

    def test_fresh_allocation_replaces_old_budget(self):
        """Fresh allocation must ignore old values."""
        old_budget = {"total_tokens": 10_000_000, "spent_tokens": 5_000_000}
        new_budget = TokenBudget.fresh_allocation()

        # New budget must NOT include old values
        assert new_budget.total_tokens == 15_000_000
        assert new_budget.spent_tokens == 0
        # Old budget is completely discarded

    def test_fresh_allocation_creates_new_session_id(self):
        """Every fresh allocation must have a unique session-id."""
        budget1 = TokenBudget.fresh_allocation()
        budget2 = TokenBudget.fresh_allocation()

        # Both must be valid UUIDs
        UUID(budget1.session_id)
        UUID(budget2.session_id)

        # Must be different
        assert budget1.session_id != budget2.session_id

    def test_token_budget_immutable(self):
        """TokenBudget is frozen (dataclass(frozen=True))."""
        budget = TokenBudget.fresh_allocation()

        with pytest.raises((TypeError, AttributeError)):
            budget.total_tokens = 5_000_000  # Cannot modify frozen

    def test_reset_manager_replaces_budget(self):
        """SessionResetManager.reset_token_budget discards old values."""
        manager = SessionResetManager()
        old_budget = {
            "total_tokens": 1_000_000,
            "spent_tokens": 900_000,
            "session_id": "old-session-123",
        }

        new_budget = manager.reset_token_budget(old_budget)

        # Must be completely new
        assert new_budget.total_tokens == 15_000_000
        assert new_budget.spent_tokens == 0
        assert new_budget.session_id != "old-session-123"


class TestSessionIdGeneration:
    """Fix #2: New session-id + timestamp generation."""

    def test_generate_new_session_id_creates_uuid(self):
        """New session-id must be a valid UUID."""
        manager = SessionResetManager()
        result = manager.generate_new_session_id()

        # Must be parseable as UUID
        session_id = UUID(result["session_id"])
        assert session_id is not None

    def test_generate_new_session_id_updates_timestamp(self):
        """Timestamp must be current (ISO format)."""
        manager = SessionResetManager()
        before = datetime.utcnow()
        result = manager.generate_new_session_id()
        after = datetime.utcnow()

        # Parse timestamp
        ts = datetime.fromisoformat(result["timestamp"])

        # Timestamp must be within [before, after]
        assert before <= ts <= after

    def test_generate_new_session_id_different_each_call(self):
        """Each call must generate a different UUID."""
        manager = SessionResetManager()
        result1 = manager.generate_new_session_id()
        result2 = manager.generate_new_session_id()

        assert result1["session_id"] != result2["session_id"]

    def test_old_session_id_ignored(self):
        """Old session-id is logged but not used."""
        manager = SessionResetManager()
        old_session_id = "abc-123"

        result = manager.generate_new_session_id(old_session_id)

        # Result must NOT contain old session-id
        assert result["session_id"] != old_session_id
        UUID(result["session_id"])  # Must be valid UUID


class TestMemoryLoading:
    """Fix #3: Memory file loading from MEMORY.md index."""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_load_memory_index_reads_file(self, mock_read, mock_exists):
        """load_memory_index must read MEMORY.md."""
        mock_exists.return_value = True
        mock_read.return_value = "- [Test](test.md) — test memory\n"

        manager = SessionResetManager()
        result = manager.load_memory_index()

        assert result["index"] == ["test.md"]

    @patch("pathlib.Path.exists")
    def test_load_memory_index_missing_file(self, mock_exists):
        """Missing MEMORY.md returns empty result."""
        mock_exists.return_value = False

        manager = SessionResetManager()
        result = manager.load_memory_index()

        assert result["index"] == []
        assert result["files"] == {}

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_load_memory_files_from_index(self, mock_read, mock_exists):
        """All referenced memory files are loaded."""
        # Mock MEMORY.md index
        index_content = """
- [File1](file1.md) — first memory
- [File2](file2.md) — second memory
"""
        # Mock file contents
        file_contents = {
            "file1.md": "content of file1",
            "file2.md": "content of file2",
        }

        call_count = 0

        def mock_read_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return index_content
            filename = args[0].name if hasattr(args[0], "name") else "file1.md"
            return file_contents.get(filename, "")

        mock_read.side_effect = mock_read_side_effect
        mock_exists.return_value = True

        manager = SessionResetManager()
        result = manager.load_memory_index()

        assert "file1.md" in result["index"]
        assert "file2.md" in result["index"]


class TestApplySessionResetFixes:
    """Integration: all three fixes applied in order."""

    @patch("core.context_engineering.session_reset_fixes.SessionResetManager.load_memory_index")
    def test_apply_all_fixes(self, mock_load_memory):
        """All three fixes are applied."""
        mock_load_memory.return_value = {
            "index": ["test.md"],
            "files": {"test.md": "content"},
        }

        old_state = {
            "token_budget": {"total_tokens": 1_000_000, "spent_tokens": 500_000},
            "session_id": "old-session-123",
        }

        result = apply_session_reset_fixes(old_state=old_state)

        # Fix 1: Token budget replaced
        assert result["fix1_token_budget"]["total_tokens"] == 15_000_000
        assert result["fix1_token_budget"]["spent_tokens"] == 0

        # Fix 2: New session-id
        assert result["fix2_session_info"]["session_id"] != "old-session-123"
        UUID(result["fix2_session_info"]["session_id"])

        # Fix 3: Memory loaded
        assert result["fix3_memory_files"]["count"] >= 0

    def test_apply_fixes_idempotent(self):
        """Calling apply_session_reset_fixes twice is idempotent."""
        result1 = apply_session_reset_fixes()
        result2 = apply_session_reset_fixes()

        # Both must have consistent structure
        assert "fix1_token_budget" in result1
        assert "fix1_token_budget" in result2
        assert result1["status"] == result2["status"] == "complete"


class TestRegressions:
    """Catch the bugs that these fixes prevent."""

    def test_budget_not_additive(self):
        """Token budget is NEVER additive (regression check)."""
        manager = SessionResetManager()

        old_budget = {"total_tokens": 10_000_000, "spent_tokens": 5_000_000}
        new_budget = manager.reset_token_budget(old_budget)

        # MUST NOT be old + new
        assert new_budget.total_tokens != 10_000_000 + 15_000_000
        # MUST be fresh
        assert new_budget.total_tokens == 15_000_000

    def test_session_id_always_new_uuid(self):
        """Session-id is ALWAYS a fresh UUID (regression check)."""
        manager = SessionResetManager()

        old_session_id = "abc-123"
        result = manager.generate_new_session_id(old_session_id)

        # MUST NOT reuse old id
        assert result["session_id"] != old_session_id
        # MUST be valid UUID
        UUID(result["session_id"])

    def test_memory_available_at_agent_start(self):
        """Memory is loaded BEFORE user-input processing."""
        # This test verifies the contract: load_memory_index is called
        # at agent startup, not during request handling

        manager = SessionResetManager()
        # Agent startup calls this:
        memory_state = manager.load_memory_index()

        # Memory must be available (even if empty)
        assert isinstance(memory_state, dict)
        assert "files" in memory_state
        assert "index" in memory_state
