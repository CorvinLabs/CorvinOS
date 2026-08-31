"""Regression: `corvin-uninstall` must reset onboarding + engine selection
even when the user declines every other purge prompt.

Before this fix, the onboarding-complete markers (``.corvin_setup_complete``,
``onboarding.json``) and the tenant's selected engine (``tenant.corvin.yaml``
``spec.default_engine``) only got deleted if the user opted into the
"Delete voice config" / "Delete Corvin home" prompts (both default to
keep-on-Enter, `[y/N]`). A user who just ran `corvin-uninstall` and pressed
Enter through every prompt (very plausible — that's what most people do)
kept their engine selection and onboarding-complete flag, so a subsequent
reinstall silently skipped onboarding and reused the old engine, even though
the whole point of uninstall+reinstall is a clean slate. These are UI/session
state, not secrets — nothing is lost by always resetting them.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from corvinOS.installer.core import CorvinInstaller


def _make_installer(tmpdir: Path) -> CorvinInstaller:
    # Isolate EVERY root uninstall() deletes from (see
    # test_uninstall_windows_autostart.py's _make_installer for why this
    # matters — an under-isolated helper here would touch the real dev
    # checkout's live .corvin / systemd units / Claude plugin cache).
    installer = CorvinInstaller(interactive=False, repo_root=tmpdir / "repo")
    installer.corvin_home = tmpdir / "corvin_home"
    installer.voice_config = tmpdir / "voice_config"
    installer.systemd_user_dir = tmpdir / "systemd_user"
    installer.claude_plugins_dir = tmpdir / "claude_plugins"
    installer.win_startup_dir = tmpdir / "win_startup"
    installer.win_desktop_dir = tmpdir / "win_desktop"
    installer.corvin_home.mkdir(parents=True, exist_ok=True)
    installer.voice_config.mkdir(parents=True, exist_ok=True)
    installer.service_manager = mock.MagicMock()
    installer.bridge_manager = mock.MagicMock()
    return installer


def _seed_onboarding_state(installer: CorvinInstaller, tenant_id: str = "_default") -> dict:
    global_dir = installer.corvin_home / "tenants" / tenant_id / "global"
    global_dir.mkdir(parents=True, exist_ok=True)

    setup_complete_flag = installer.voice_config / ".corvin_setup_complete"
    setup_complete_flag.touch()

    onboarding_json = global_dir / "onboarding.json"
    onboarding_json.write_text(json.dumps({"complete": True, "completed_at": "2026-07-12T00:00:00Z"}))

    tenant_yaml = global_dir / "tenant.corvin.yaml"
    tenant_yaml.write_text(
        "spec:\n"
        "  default_engine: hermes\n"
        "  data_residency: eu\n"  # an unrelated setting that must survive
    )
    return {
        "setup_complete_flag": setup_complete_flag,
        "onboarding_json": onboarding_json,
        "tenant_yaml": tenant_yaml,
    }


class TestUninstallResetsOnboarding:
    def test_reset_happens_even_when_every_other_prompt_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            paths = _seed_onboarding_state(installer)

            # Decline every other purge prompt ("n" / Enter-through-default) —
            # the onboarding/engine reset must still happen unconditionally.
            with mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=False)

            assert not paths["setup_complete_flag"].exists(), (
                ".corvin_setup_complete must be removed regardless of the "
                "voice-config purge prompt answer"
            )
            assert not paths["onboarding_json"].exists(), (
                "onboarding.json must be removed regardless of the "
                "corvin-home purge prompt answer"
            )
            assert paths["tenant_yaml"].exists(), (
                "tenant.corvin.yaml itself must survive a declined purge — "
                "only the engine selection inside it is reset"
            )
            doc = paths["tenant_yaml"].read_text()
            assert "default_engine" not in doc, "engine selection must be cleared"
            assert "data_residency: eu" in doc, (
                "unrelated tenant settings must NOT be silently wiped by the "
                "onboarding reset"
            )

    def test_reset_also_happens_with_purge_true(self) -> None:
        # With purge=True the whole corvin_home/voice_config trees are wiped
        # anyway, but the reset step must not error when run first against
        # state that a moment later gets deleted wholesale.
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            _seed_onboarding_state(installer)
            installer.uninstall(purge=True)
            assert not installer.corvin_home.exists()
            assert not installer.voice_config.exists()

    def test_multi_tenant_all_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            paths_default = _seed_onboarding_state(installer, "_default")
            paths_other = _seed_onboarding_state(installer, "other_tenant")

            with mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=False)

            for paths in (paths_default, paths_other):
                assert not paths["onboarding_json"].exists()
                assert "default_engine" not in paths["tenant_yaml"].read_text()

    def test_nothing_to_reset_does_not_crash(self) -> None:
        """A fresh/never-onboarded install has none of these files yet —
        the reset step must be a silent no-op, not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            installer = _make_installer(Path(tmp))
            with mock.patch("builtins.input", return_value="n"):
                installer.uninstall(purge=False)  # must not raise


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
