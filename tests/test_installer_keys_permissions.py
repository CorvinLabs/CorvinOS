"""Regression: API keys must never sit on disk at a permissive mode, even
transiently (adversarial review finding).

Before this fix, `save_keys()` created `service.env` via a bare
`Path.touch()` (inheriting the process's default umask, typically
world/group-readable on Linux) and only narrowed it to 0600 AFTER the full
multi-key write sequence completed — so plaintext API keys were flushed to
disk at a permissive mode for the entire prompt/validate/write duration, a
real (non-nanosecond) exposure window on a shared/multi-user host.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import keys as keys_mod


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
class TestSaveKeysPermissions:
    def test_env_file_is_never_observed_at_a_permissive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "service.env"
            observed_modes: list[int] = []

            real_write_text = Path.write_text

            def spying_write_text(self, *a, **kw):  # noqa: ANN001
                result = real_write_text(self, *a, **kw)
                if self == env_file:
                    observed_modes.append(_mode(self))
                return result

            with mock.patch.object(keys_mod, "ENV_FILE", env_file), \
                 mock.patch.object(Path, "write_text", spying_write_text):
                keys_mod.save_keys("sk-openai-test", "sk-ant-test")

            assert observed_modes, "no writes were observed"
            assert all(m == 0o600 for m in observed_modes), (
                f"service.env was written at a permissive mode at some point: "
                f"{oct(max(observed_modes))}"
            )
            assert _mode(env_file) == 0o600

    def test_narrows_mode_of_a_preexisting_permissive_file_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "service.env"
            env_file.write_text("")
            env_file.chmod(0o644)  # simulate a leftover permissive file
            assert _mode(env_file) == 0o644

            with mock.patch.object(keys_mod, "ENV_FILE", env_file):
                keys_mod.save_keys("sk-openai-test", "")

            assert _mode(env_file) == 0o600

    def test_repo_env_copy_is_narrowed_before_secrets_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            repo_root.mkdir()
            example = repo_root / ".env.example"
            example.write_text("OPENAI_API_KEY=\n")
            example.chmod(0o644)

            env_file = Path(tmp) / "service.env"
            with mock.patch.object(keys_mod, "ENV_FILE", env_file):
                keys_mod.save_keys("sk-openai-test", "", repo_root=repo_root)

            repo_env = repo_root / ".env"
            assert repo_env.exists()
            assert _mode(repo_env) == 0o600
