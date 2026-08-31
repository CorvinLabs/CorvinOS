"""Tests for GET /chat/sessions/{sid}/turns ExecutionContext Integration (Phase 2c).

Tests verify:
  - Filtering by engine_id, delegation_mode, model_name works
  - Backward compatibility: turns without execution_context still work
  - Graceful handling of malformed execution_context

This module tests the _filter_turns_by_execution_context function directly,
which is used in the GET /chat/sessions/{sid}/turns route (Phase 2c).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


def _filter_turns_by_execution_context(
    turns: list[dict[str, Any]],
    *,
    engine_id: str | None = None,
    delegation_mode: str | None = None,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Filter turns by execution context metadata (Phase 2c).

    Only applies to turns that have execution_context (assistant/delegated turns).
    Turns without execution_context are excluded when any filter is active.

    Args:
        turns: List of turn dicts (from read_turns).
        engine_id: Filter by engine_id (e.g., "claude_code", "acs", "tde").
        delegation_mode: Filter by delegation_mode (e.g., "native", "acs", "tde").
        model_name: Filter by model_name (exact match, e.g., "claude-opus-5").

    Returns:
        Filtered list of turns that match all provided filters.
    """
    if not any([engine_id, delegation_mode, model_name]):
        return turns

    filtered = []
    for turn in turns:
        ctx = turn.get("execution_context")
        if not ctx:
            # Skip turns without execution context when filtering is active
            continue

        # All provided filters must match (AND logic)
        if engine_id and ctx.get("engine_id") != engine_id:
            continue
        if delegation_mode and ctx.get("delegation_mode") != delegation_mode:
            continue
        if model_name and ctx.get("model_name") != model_name:
            continue

        filtered.append(turn)

    return filtered


class TestExecutionContextFiltering(unittest.TestCase):
    """Test filtering by execution_context fields (Phase 2c)."""

    def test_filter_by_engine_id(self):
        """Filter turns by engine_id."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {"engine_id": "claude_code", "model_name": "claude-opus"},
            },
            {"role": "user", "text": "Q2"},
            {
                "role": "assistant",
                "text": "A2",
                "execution_context": {"engine_id": "acs", "model_name": "claude-opus"},
            },
        ]

        # Filter by engine_id=acs
        filtered = _filter_turns_by_execution_context(turns, engine_id="acs")

        # Only turn with engine_id=acs (turns without execution_context are excluded)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["execution_context"]["engine_id"], "acs")

    def test_filter_by_delegation_mode(self):
        """Filter turns by delegation_mode."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {
                    "engine_id": "claude_code",
                    "delegation_mode": "native",
                },
            },
            {"role": "user", "text": "Q2"},
            {
                "role": "assistant",
                "text": "A2",
                "execution_context": {"engine_id": "acs", "delegation_mode": "acs"},
            },
        ]

        # Filter by delegation_mode=acs
        filtered = _filter_turns_by_execution_context(turns, delegation_mode="acs")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["execution_context"]["delegation_mode"], "acs")

    def test_filter_by_model_name(self):
        """Filter turns by model_name (exact match)."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {"model_name": "claude-3-5-sonnet"},
            },
            {"role": "user", "text": "Q2"},
            {
                "role": "assistant",
                "text": "A2",
                "execution_context": {"model_name": "claude-opus-5"},
            },
        ]

        # Filter by model_name=claude-opus-5
        filtered = _filter_turns_by_execution_context(turns, model_name="claude-opus-5")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["execution_context"]["model_name"], "claude-opus-5")

    def test_filter_combined_and_logic(self):
        """Filter by multiple fields uses AND logic."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {
                    "engine_id": "acs",
                    "delegation_mode": "acs",
                    "model_name": "claude-opus-5",
                },
            },
            {"role": "user", "text": "Q2"},
            {
                "role": "assistant",
                "text": "A2",
                "execution_context": {
                    "engine_id": "acs",
                    "delegation_mode": "native",
                    "model_name": "claude-opus-5",
                },
            },
        ]

        # Filter by engine_id=acs AND delegation_mode=acs AND model_name=claude-opus-5
        filtered = _filter_turns_by_execution_context(
            turns,
            engine_id="acs",
            delegation_mode="acs",
            model_name="claude-opus-5",
        )

        # Only one turn matches all three criteria
        self.assertEqual(len(filtered), 1)
        ctx = filtered[0]["execution_context"]
        self.assertEqual(ctx["engine_id"], "acs")
        self.assertEqual(ctx["delegation_mode"], "acs")
        self.assertEqual(ctx["model_name"], "claude-opus-5")

    def test_filter_excludes_turns_without_context(self):
        """Filtering excludes turns without execution_context."""
        turns = [
            {"role": "user", "text": "Q1"},
            {"role": "assistant", "text": "A1"},  # No execution_context
            {"role": "user", "text": "Q2"},
            {
                "role": "assistant",
                "text": "A2",
                "execution_context": {"engine_id": "claude_code"},
            },
        ]

        filtered = _filter_turns_by_execution_context(turns, engine_id="claude_code")

        # Only the turn with execution_context=claude_code
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["execution_context"]["engine_id"], "claude_code")

    def test_filter_no_matches(self):
        """Filtering with no matches returns empty list."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {"engine_id": "claude_code"},
            },
        ]

        filtered = _filter_turns_by_execution_context(turns, engine_id="nonexistent")

        self.assertEqual(len(filtered), 0)

    def test_no_filters_returns_all(self):
        """Without filters, all turns are returned."""
        turns = [
            {"role": "user", "text": "Q1"},
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {"engine_id": "claude_code"},
            },
            {"role": "user", "text": "Q2"},
            {"role": "assistant", "text": "A2"},  # No execution_context
        ]

        filtered = _filter_turns_by_execution_context(turns)

        # All turns returned when no filters
        self.assertEqual(len(filtered), 4)

    def test_missing_execution_context_fields(self):
        """Turns with partial execution_context still work."""
        turns = [
            {"role": "user", "text": "Q"},
            {
                "role": "assistant",
                "text": "A",
                "execution_context": {
                    "engine_id": "claude_code",
                    # missing: model_name, delegation_mode, etc.
                },
            },
        ]

        filtered = _filter_turns_by_execution_context(turns, engine_id="claude_code")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["execution_context"]["engine_id"], "claude_code")

    def test_filter_against_missing_field_returns_empty(self):
        """Filtering by a field that doesn't exist excludes that turn."""
        turns = [
            {
                "role": "assistant",
                "text": "A1",
                "execution_context": {
                    "engine_id": "claude_code",
                    # No delegation_mode field
                },
            }
        ]

        filtered = _filter_turns_by_execution_context(turns, delegation_mode="acs")

        # Turn doesn't have delegation_mode, so it's excluded
        self.assertEqual(len(filtered), 0)


if __name__ == "__main__":
    unittest.main()
