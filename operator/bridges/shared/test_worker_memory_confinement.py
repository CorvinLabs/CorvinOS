"""ADR-0052 F5 — E2E tests for worker memory path confinement.

Tests cover:
  - _validate_memory_write_path allows paths inside allowed_root
  - _validate_memory_write_path raises WorkerMemoryPathEscape for ../traversal
  - _write_cc_memory_files respects the confinement guard
  - session_mem_dir is created with mode 0o700
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from memory_bridge import (
    WorkerMemoryPathEscape,
    _validate_memory_write_path,
    _write_cc_memory_files,
)


class TestValidateMemoryWritePath:
    def test_allow_child_path(self, tmp_path):
        root = tmp_path / "session" / ".claude" / "memory"
        root.mkdir(parents=True)
        target = root / "corvin_user_profile.md"
        _validate_memory_write_path(target, root)  # must not raise

    def test_allow_nested_child(self, tmp_path):
        root = tmp_path / "session" / ".claude" / "memory"
        root.mkdir(parents=True)
        (root / "subdir").mkdir()
        target = root / "subdir" / "file.md"
        _validate_memory_write_path(target, root)

    def test_deny_parent_traversal(self, tmp_path):
        root = tmp_path / "session" / ".claude" / "memory"
        root.mkdir(parents=True)
        # ../../../ escapes the session dir
        target = root / ".." / ".." / ".." / "escaped.md"
        with pytest.raises(WorkerMemoryPathEscape):
            _validate_memory_write_path(target, root)

    def test_deny_sibling_session(self, tmp_path):
        sessions = tmp_path / "sessions"
        session_a = sessions / "bridge:chat_a" / ".claude" / "memory"
        session_b = sessions / "bridge:chat_b" / ".claude" / "memory"
        session_a.mkdir(parents=True)
        session_b.mkdir(parents=True)
        # Worker in session A should not write to session B
        target = session_b / "poisoned.md"
        with pytest.raises(WorkerMemoryPathEscape):
            _validate_memory_write_path(target, session_a)

    def test_allow_exact_root(self, tmp_path):
        root = tmp_path / "memory"
        root.mkdir(parents=True)
        # Writing a file directly in root is allowed
        target = root / "file.md"
        _validate_memory_write_path(target, root)


class TestWriteCcMemoryFiles:
    def test_creates_directory_mode_0700(self, tmp_path):
        session_mem_dir = tmp_path / ".claude" / "memory"
        _write_cc_memory_files(
            session_mem_dir,
            user_profile="Hello user",
            project_facts="Project X",
            session_learnings="Learned Y",
        )
        assert session_mem_dir.exists()
        mode = stat.S_IMODE(session_mem_dir.stat().st_mode)
        assert mode == 0o700, f"Expected mode 0o700, got {oct(mode)}"

    def test_writes_expected_files(self, tmp_path):
        session_mem_dir = tmp_path / ".claude" / "memory"
        _write_cc_memory_files(
            session_mem_dir,
            user_profile="User profile text",
            project_facts="",
            session_learnings="Session learning text",
        )
        assert (session_mem_dir / "corvin_user_profile.md").exists()
        assert not (session_mem_dir / "corvin_project_facts.md").exists()
        assert (session_mem_dir / "corvin_session_learnings.md").exists()

    def test_no_path_escape_via_crafted_filenames(self, tmp_path):
        """Path escape should be blocked even if filenames are hardcoded;
        this test verifies the validation guard does not false-positive on
        the legitimate fixed filenames the function uses."""
        session_mem_dir = tmp_path / ".claude" / "memory"
        # Must complete without raising WorkerMemoryPathEscape
        _write_cc_memory_files(
            session_mem_dir,
            user_profile="ok",
            project_facts="ok",
            session_learnings="ok",
        )
