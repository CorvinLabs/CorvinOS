"""Unit tests for context_checkpoint module (ADR-0087 M1)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from context_checkpoint import (
    load_checkpoint,
    save_checkpoint,
    clear_checkpoints,
    _checkpoint_path,
)


def test_save_and_load_checkpoint():
    """Test that checkpoints are saved and loaded correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # Save a checkpoint
        save_checkpoint(
            session_dir,
            engine_id="codex_cli",
            turn_id="t_123",
            system_prompt_summary="You are helpful",
            last_message_summary="What is AI?",
            tool_results_digest="[get_definition: successful]",
            conversation_length_tokens=450,
        )

        # Load it back
        loaded = load_checkpoint(session_dir)
        assert loaded is not None
        assert loaded["engine_id"] == "codex_cli"
        assert loaded["turn_id"] == "t_123"
        assert loaded["system_prompt_summary"] == "You are helpful"
        assert loaded["last_message_summary"] == "What is AI?"


def test_load_checkpoint_nonexistent():
    """Test that loading from empty session returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)
        loaded = load_checkpoint(session_dir)
        assert loaded is None


def test_load_latest_checkpoint():
    """Test that loading returns the most recent checkpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # Save two checkpoints
        save_checkpoint(
            session_dir,
            engine_id="codex_cli",
            turn_id="t_1",
            system_prompt_summary="v1",
            last_message_summary="msg1",
            tool_results_digest="",
            conversation_length_tokens=100,
        )

        save_checkpoint(
            session_dir,
            engine_id="opencode_cli",
            turn_id="t_2",
            system_prompt_summary="v2",
            last_message_summary="msg2",
            tool_results_digest="",
            conversation_length_tokens=200,
        )

        # Load should return the latest (t_2)
        loaded = load_checkpoint(session_dir)
        assert loaded["turn_id"] == "t_2"
        assert loaded["engine_id"] == "opencode_cli"


def test_clear_checkpoints():
    """Test that clear_checkpoints removes the log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        save_checkpoint(
            session_dir,
            engine_id="hermes_engine",
            turn_id="t_1",
            system_prompt_summary="test",
            last_message_summary="msg",
            tool_results_digest="",
            conversation_length_tokens=50,
        )

        assert _checkpoint_path(session_dir).exists()

        clear_checkpoints(session_dir)

        assert not _checkpoint_path(session_dir).exists()


def test_checkpoint_appends_atomically():
    """Test that multiple saves append correctly and are idempotent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # Save multiple checkpoints
        for i in range(3):
            save_checkpoint(
                session_dir,
                engine_id="claude_code",
                turn_id=f"t_{i}",
                system_prompt_summary=f"summary_{i}",
                last_message_summary=f"msg_{i}",
                tool_results_digest="",
                conversation_length_tokens=100 + i,
            )

        # Load latest
        loaded = load_checkpoint(session_dir)
        assert loaded["turn_id"] == "t_2"

        # Check log has 3 lines
        path = _checkpoint_path(session_dir)
        with open(path) as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 3

        # Verify each line is valid JSON
        for line in lines:
            obj = json.loads(line)
            assert "turn_id" in obj
