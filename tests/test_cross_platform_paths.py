"""Cross-platform path resolution tests for CorvinOS.

Simulates Windows, macOS, and Linux environments by patching
platform.system() and os.environ so these tests run on any OS.
Covers every public resolver in forge/paths.py and corvinOS/shared/paths.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# ── Path setup: load the canonical path resolvers ────────────────────────

_REPO = Path(__file__).resolve().parents[1]
_FORGE = _REPO / "operator" / "forge"
_CORVIN_SHARED = _REPO / "corvinOS" / "shared"

for _p in [str(_FORGE), str(_CORVIN_SHARED)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forge import paths as forge_paths  # noqa: E402

# corvinOS.shared.paths may not be importable as a package module
# when running from the repo root; fall back to direct file import.
try:
    from corvinOS.shared import paths as shared_paths  # type: ignore
except ModuleNotFoundError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "shared_paths", _CORVIN_SHARED / "paths.py"
    )
    shared_paths = _ilu.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(shared_paths)  # type: ignore


# ── Helpers ───────────────────────────────────────────────────────────────

class _FakeHome:
    """Context manager that patches Path.home() to return a tmp path."""
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self._patch = mock.patch.object(Path, "home", return_value=tmp_path)

    def __enter__(self):
        self._patch.start()
        return self.tmp

    def __exit__(self, *_):
        self._patch.stop()


# ── forge/paths.py — corvin_home() ───────────────────────────────────────

class TestForgeCorvinHome:
    def test_env_var_overrides_everything(self, tmp_path):
        target = tmp_path / "custom-corvin"
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(target)}):
            assert forge_paths.corvin_home() == target

    def test_env_var_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CORVIN_HOME", "~/my-corvin")
        result = forge_paths.corvin_home()
        assert str(result).startswith(str(tmp_path))
        assert "my-corvin" in str(result)

    def test_fallback_to_home_dotcorvin(self, tmp_path):
        with mock.patch.dict(os.environ, {}, clear=False):
            env = {k: v for k, v in os.environ.items() if k != "CORVIN_HOME"}
            with mock.patch.dict(os.environ, env, clear=True):
                with _FakeHome(tmp_path):
                    # Patch _repo_root to return None (not in a repo)
                    with mock.patch.object(forge_paths, "_repo_root", return_value=None):
                        result = forge_paths.corvin_home()
                        assert result == tmp_path / ".corvin"

    def test_repo_detection_wins_over_home(self, tmp_path):
        repo = tmp_path / "myrepo"
        repo_corvin = repo / ".corvin"
        repo_corvin.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k != "CORVIN_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(forge_paths, "_repo_root", return_value=repo):
                result = forge_paths.corvin_home()
                assert result == repo_corvin


# ── forge/paths.py — voice_config_dir() ─────────────────────────────────

class TestForgeVoiceConfigDir:
    """Simulate all three platforms by patching platform.system()."""

    def test_linux_returns_xdg_config(self, tmp_path):
        with mock.patch("platform.system", return_value="Linux"):
            with _FakeHome(tmp_path):
                result = forge_paths.voice_config_dir()
                assert result == tmp_path / ".config" / "corvin-voice"

    def test_macos_returns_xdg_config(self, tmp_path):
        # CorvinOS intentionally uses ~/.config on macOS (not ~/Library)
        with mock.patch("platform.system", return_value="Darwin"):
            with _FakeHome(tmp_path):
                result = forge_paths.voice_config_dir()
                assert result == tmp_path / ".config" / "corvin-voice"

    def test_windows_uniform_dotconfig(self, tmp_path):
        """SSOT (path-audit 2026-07-06): Windows uses the SAME ~/.config/corvin-voice
        as every other platform — the former %APPDATA%\\Local branch made the console
        write a dir the installer + voice scripts never read (reader≠writer)."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("APPDATA", "VOICE_CONFIG_DIR", "XDG_CONFIG_HOME")}
        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch.dict(os.environ, env, clear=True):
                with _FakeHome(tmp_path):
                    result = forge_paths.voice_config_dir()
                    assert result == tmp_path / ".config" / "corvin-voice"

    def test_voice_config_dir_env_override(self, tmp_path):
        """VOICE_CONFIG_DIR override wins on every platform."""
        override = tmp_path / "pinned" / "corvin-voice"
        for plat in ("Linux", "Darwin", "Windows"):
            with mock.patch("platform.system", return_value=plat):
                with mock.patch.dict(os.environ, {"VOICE_CONFIG_DIR": str(override)}):
                    assert forge_paths.voice_config_dir() == override

    def test_voice_config_dir_honors_xdg(self, tmp_path):
        """XDG_CONFIG_HOME is honored (no VOICE_CONFIG_DIR set)."""
        xdg = tmp_path / "xdgcfg"
        env = {k: v for k, v in os.environ.items() if k != "VOICE_CONFIG_DIR"}
        env["XDG_CONFIG_HOME"] = str(xdg)
        with mock.patch.dict(os.environ, env, clear=True):
            assert forge_paths.voice_config_dir() == xdg / "corvin-voice"


# ── corvinOS/shared/paths.py — same guarantees ───────────────────────────

class TestSharedPathsCorvinHome:
    def test_env_var_takes_priority(self, tmp_path):
        target = tmp_path / "shared-corvin"
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(target)}):
            assert shared_paths.corvin_home() == target

    def test_fallback_to_home_dotcorvin(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "CORVIN_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            with _FakeHome(tmp_path):
                with mock.patch.object(shared_paths, "_repo_root", return_value=None):
                    result = shared_paths.corvin_home()
                    assert result == tmp_path / ".corvin"


class TestSharedPathsVoiceConfigDir:
    def test_linux(self, tmp_path):
        with mock.patch("platform.system", return_value="Linux"):
            with _FakeHome(tmp_path):
                result = shared_paths.voice_config_dir()
                assert result == tmp_path / ".config" / "corvin-voice"

    def test_windows_uniform_dotconfig(self, tmp_path):
        """SSOT: shared/paths matches forge/paths — uniform ~/.config on Windows too."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("APPDATA", "VOICE_CONFIG_DIR", "XDG_CONFIG_HOME")}
        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch.dict(os.environ, env, clear=True):
                with _FakeHome(tmp_path):
                    result = shared_paths.voice_config_dir()
                    assert result == tmp_path / ".config" / "corvin-voice"


# ── Path consistency: forge and shared agree ─────────────────────────────

class TestPathConsistency:
    """forge/paths.py and corvinOS/shared/paths.py must agree on all platforms."""

    @pytest.mark.parametrize("platform_name", ["Linux", "Darwin", "Windows"])
    def test_voice_config_dir_consistent(self, tmp_path, platform_name):
        env = {k: v for k, v in os.environ.items()
               if k not in ("APPDATA", "VOICE_CONFIG_DIR", "XDG_CONFIG_HOME")}
        with mock.patch("platform.system", return_value=platform_name):
            with mock.patch.dict(os.environ, env, clear=True):
                with _FakeHome(tmp_path):
                    forge_result = forge_paths.voice_config_dir()
                    shared_result = shared_paths.voice_config_dir()
                    assert forge_result == shared_result, (
                        f"forge/paths and shared/paths disagree on {platform_name}: "
                        f"{forge_result} != {shared_result}"
                    )

    def test_corvin_home_consistent(self, tmp_path):
        target = tmp_path / "custom"
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(target)}):
            assert forge_paths.corvin_home() == shared_paths.corvin_home()


# ── Tenant path resolution (ADR-0007) ────────────────────────────────────

class TestTenantPaths:
    def test_tenant_global_dir(self, tmp_path):
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            result = forge_paths.tenant_global_dir("_default")
            assert "_default" in str(result)
            assert "global" in str(result)

    def test_custom_tenant(self, tmp_path):
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            result = forge_paths.tenant_global_dir("acme")
            assert "acme" in str(result)

    def test_tenant_id_validated(self):
        """_validate_tenant_id() must reject path-traversal and reserved names."""
        with pytest.raises(Exception):
            forge_paths._validate_tenant_id("../../../etc")

    def test_tenant_id_accepts_valid(self):
        forge_paths._validate_tenant_id("_default")
        forge_paths._validate_tenant_id("acme-corp")


# ── Windows separator check ───────────────────────────────────────────────

class TestNoForwardSlashHardcoding:
    """Paths must never be built by string concatenation with '/'."""

    def test_corvin_home_is_path_object(self, tmp_path):
        with mock.patch.dict(os.environ, {"CORVIN_HOME": str(tmp_path)}):
            result = forge_paths.corvin_home()
            assert isinstance(result, Path)

    def test_voice_config_dir_is_path_object(self, tmp_path):
        with _FakeHome(tmp_path):
            result = forge_paths.voice_config_dir()
            assert isinstance(result, Path)
