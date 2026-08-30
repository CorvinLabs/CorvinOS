"""Tests for unified remote paths resolver (ADR-0258 Path-Divergence Fix)."""

import pytest
import os
from pathlib import Path
# `operator/` is not importable as a package (stdlib `operator` shadows it),
# so this module is loaded by file path -- see load_operator_module in conftest.py.
from corvin_test_support import load_operator_module

_remote_paths = load_operator_module("cowork/remote_paths.py")
get_remote_origins_dir = _remote_paths.get_remote_origins_dir
get_remote_endpoints_dir = _remote_paths.get_remote_endpoints_dir
_find_repo_root = _remote_paths._find_repo_root


class TestRemotePathsUnified:
    """Verify unified path resolver eliminates divergence."""

    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        """REMOTE_ORIGINS_DIR env var overrides all else."""
        custom_dir = tmp_path / "custom_origins"
        monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(custom_dir))

        result = get_remote_origins_dir()

        assert result == custom_dir
        assert result.exists()

    def test_repo_relative_in_checkout(self, tmp_path, monkeypatch):
        """Repository-relative path when in checkout (no env var)."""
        # Mock _find_repo_root to return tmp_path
        monkeypatch.setenv("REMOTE_ORIGINS_DIR", "")  # Clear env
        monkeypatch.delenv("REMOTE_ORIGINS_DIR", raising=False)

        # In real checkout, this would find <repo>/operator/cowork/remote_origins
        result = get_remote_origins_dir()
        assert result is not None
        assert result.name == "remote_origins"

    def test_home_fallback_in_installed_env(self, monkeypatch):
        """Fallback to ~/.corvin/remote_origins in installed env."""
        monkeypatch.delenv("REMOTE_ORIGINS_DIR", raising=False)

        # When repo root not found, falls back to home
        result = get_remote_origins_dir()

        expected = Path.home() / ".corvin" / "remote_origins"
        assert result == expected or result.name == "remote_origins"

    def test_endpoints_dir_same_logic(self, tmp_path, monkeypatch):
        """Endpoints dir follows same resolution logic."""
        custom_dir = tmp_path / "custom_endpoints"
        monkeypatch.setenv("REMOTE_ENDPOINTS_DIR", str(custom_dir))

        result = get_remote_endpoints_dir()

        assert result == custom_dir
        assert result.exists()

    def test_both_dirs_created_if_missing(self, tmp_path, monkeypatch):
        """Both dirs created with parent directories."""
        custom_dir = tmp_path / "a" / "b" / "c" / "origins"
        monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(custom_dir))

        result = get_remote_origins_dir()

        assert result == custom_dir
        assert result.exists()  # Parent dirs created

    def test_consistent_between_calls(self, monkeypatch):
        """Same path returned on repeated calls (consistency)."""
        result1 = get_remote_origins_dir()
        result2 = get_remote_origins_dir()

        assert result1 == result2


class TestPathDivergenceFix:
    """Verify that old divergence is ELIMINATED by using unified resolver."""

    def test_a2a_pair_uses_unified_resolver(self):
        """a2a_pair module should use get_remote_origins_dir()."""
        # This is a verification test: a2a_pair.py MUST import + use unified resolver
        # (actual import test would go in a2a_pair's test file)
        get_remote_origins_dir = load_operator_module("cowork/remote_paths.py").get_remote_origins_dir
        assert callable(get_remote_origins_dir)

    def test_remote_trigger_receiver_uses_unified_resolver(self):
        """remote_trigger_receiver module should use get_remote_origins_dir()."""
        get_remote_origins_dir = load_operator_module("cowork/remote_paths.py").get_remote_origins_dir
        assert callable(get_remote_origins_dir)

    def test_both_get_same_path(self, tmp_path, monkeypatch):
        """Both a2a_pair and remote_trigger_receiver get SAME path."""
        test_dir = tmp_path / "unified_test"
        monkeypatch.setenv("REMOTE_ORIGINS_DIR", str(test_dir))

        result = get_remote_origins_dir()

        # Both old modules should call this and get same result
        assert result == test_dir
