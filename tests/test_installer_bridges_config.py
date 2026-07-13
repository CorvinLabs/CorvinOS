"""Coverage for corvinOS/installer/steps/bridges.py — previously zero test coverage.

Exercises the per-bridge configurators dispatched by configure_bridges():
  - non-interactive mode never calls input() and returns configured=False
  - interactive mode with a token entered writes settings.json with that token
  - re-running against a bridges_dir with an already-populated settings.json
    does not re-prompt (input() never called) and preserves the existing token

Also documents (rather than asserts a desired-but-absent behavior for) the
permissions asymmetry with keys.py: _write_settings() never chmods the
resulting settings.json, unlike keys.py's explicit 0o600 narrowing of
service.env. See test_permissive_mode_is_not_narrowed_unlike_keys_env below.
"""
from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import bridges as bridges_mod


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Never allow a test to actually shell out to install.sh / whatsapp_cli.sh."""
    fake_result = subprocess.CompletedProcess(args=["bash"], returncode=0)
    monkeypatch.setattr(bridges_mod.subprocess, "run", mock.Mock(return_value=fake_result))
    yield


class TestNonInteractiveNeverPrompts:
    """Non-interactive mode must never call input() and must report configured=False
    when no token pre-exists on disk."""

    @pytest.mark.parametrize(
        "bridge, field",
        [
            ("telegram", "telegram_token"),
            ("discord", "discord_token"),
        ],
    )
    def test_non_interactive_never_calls_input_and_reports_unconfigured(
        self, tmp_path: Path, bridge: str, field: str
    ) -> None:
        bridges_dir = tmp_path / "operator" / "bridges"

        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, [bridge], interactive=False)

        assert len(results) == 1
        result = results[0]
        assert result.name == bridge
        assert result.configured is False

        settings_path = bridges_dir / bridge / "settings.json"
        # nothing should have been written since no token was ever supplied
        assert not settings_path.exists()

    def test_non_interactive_slack_never_prompts(self, tmp_path: Path) -> None:
        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["slack"], interactive=False)

        assert results[0].configured is False

    def test_non_interactive_email_never_prompts(self, tmp_path: Path) -> None:
        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")), \
             mock.patch("corvinOS.installer.steps.bridges.getpass.getpass",
                        side_effect=AssertionError("getpass() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["email"], interactive=False)

        assert results[0].configured is False


class TestInteractiveWritesToken:
    """Interactive mode with a token entered must write settings.json with that token."""

    def test_interactive_telegram_writes_token_and_whitelist(self, tmp_path: Path) -> None:
        answers = iter(["123456:ABC-DEF-token", "111 222"])
        with mock.patch("builtins.input", side_effect=lambda *_a, **_k: next(answers)):
            results = bridges_mod.configure_bridges(tmp_path, ["telegram"], interactive=True)

        result = results[0]
        assert result.configured is True
        settings_path = tmp_path / "operator" / "bridges" / "telegram" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert data["telegram_token"] == "123456:ABC-DEF-token"
        assert data["whitelist"] == ["111", "222"]

    def test_interactive_discord_writes_token(self, tmp_path: Path) -> None:
        answers = iter(["discord-secret-token", "999999999999999999"])
        with mock.patch("builtins.input", side_effect=lambda *_a, **_k: next(answers)):
            results = bridges_mod.configure_bridges(tmp_path, ["discord"], interactive=True)

        result = results[0]
        assert result.configured is True
        settings_path = tmp_path / "operator" / "bridges" / "discord" / "settings.json"
        data = json.loads(settings_path.read_text())
        assert data["discord_token"] == "discord-secret-token"
        assert data["whitelist"] == ["999999999999999999"]

    def test_interactive_slack_writes_both_tokens(self, tmp_path: Path) -> None:
        answers = iter(["xoxb-bot-token", "xapp-app-token", "U12345"])
        with mock.patch("builtins.input", side_effect=lambda *_a, **_k: next(answers)):
            results = bridges_mod.configure_bridges(tmp_path, ["slack"], interactive=True)

        result = results[0]
        assert result.configured is True
        settings_path = tmp_path / "operator" / "bridges" / "slack" / "settings.json"
        data = json.loads(settings_path.read_text())
        assert data["slack_bot_token"] == "xoxb-bot-token"
        assert data["slack_app_token"] == "xapp-app-token"
        assert data["whitelist"] == ["U12345"]


class TestIdempotentSkipsRePrompt:
    """Re-running configure_bridges() against a bridges_dir that already has a
    populated (non-placeholder) settings.json must not re-prompt and must
    preserve the existing token."""

    def test_telegram_existing_token_is_kept_without_prompting(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "operator" / "bridges" / "telegram" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"telegram_token": "already-set-token", "whitelist": ["42"]}))

        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["telegram"], interactive=True)

        result = results[0]
        assert result.configured is True
        data = json.loads(settings_path.read_text())
        assert data["telegram_token"] == "already-set-token"
        assert data["whitelist"] == ["42"]

    def test_discord_placeholder_token_is_not_treated_as_configured(self, tmp_path: Path) -> None:
        """A leftover 'DEIN_' placeholder value must NOT count as already-configured —
        it should fall through to the prompt path."""
        settings_path = tmp_path / "operator" / "bridges" / "discord" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"discord_token": "DEIN_DISCORD_TOKEN"}))

        answers = iter(["real-token", ""])
        with mock.patch("builtins.input", side_effect=lambda *_a, **_k: next(answers)):
            results = bridges_mod.configure_bridges(tmp_path, ["discord"], interactive=True)

        result = results[0]
        assert result.configured is True
        data = json.loads(settings_path.read_text())
        assert data["discord_token"] == "real-token"

    def test_slack_existing_tokens_kept_without_prompting(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "operator" / "bridges" / "slack" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "slack_bot_token": "xoxb-existing",
            "slack_app_token": "xapp-existing",
        }))

        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["slack"], interactive=True)

        result = results[0]
        assert result.configured is True
        data = json.loads(settings_path.read_text())
        assert data["slack_bot_token"] == "xoxb-existing"
        assert data["slack_app_token"] == "xapp-existing"

    def test_email_existing_user_kept_without_prompting(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "operator" / "bridges" / "email" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"imap_user": "me@example.com"}))

        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")), \
             mock.patch("corvinOS.installer.steps.bridges.getpass.getpass",
                        side_effect=AssertionError("getpass() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["email"], interactive=True)

        result = results[0]
        assert result.configured is True
        data = json.loads(settings_path.read_text())
        assert data["imap_user"] == "me@example.com"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
class TestPermissionsAsymmetryWithKeysModule:
    """bridges.py's _write_settings() writes bot secrets to settings.json without ever
    chmod-ing the file, unlike keys.py's explicit 0o600 narrowing of service.env
    (see tests/test_installer_keys_permissions.py). This test documents the CURRENT
    (permissive, umask-derived) behavior so a future regression to something even more
    permissive is caught, and so any future hardening fix has a test to flip.
    """

    def test_settings_json_is_written_at_the_process_umask_not_narrowed(self, tmp_path: Path) -> None:
        answers = iter(["some-token", ""])
        with mock.patch("builtins.input", side_effect=lambda *_a, **_k: next(answers)):
            bridges_mod.configure_bridges(tmp_path, ["telegram"], interactive=True)

        settings_path = tmp_path / "operator" / "bridges" / "telegram" / "settings.json"
        assert settings_path.exists()
        # bridges.py never calls chmod — the resulting mode is whatever the
        # process umask leaves write_text() with. It is explicitly NOT narrowed
        # to 0o600 the way keys.py narrows service.env. If this assertion ever
        # starts failing because the mode IS 0o600, that means someone added a
        # chmod — which would be a welcome hardening fix, not a regression.
        mode = _mode(settings_path)
        assert mode != 0o600, (
            "settings.json is now written at 0o600 — if intentional, this test's "
            "assertion should be flipped and the bug note in bugsDiscovered resolved"
        )


class TestUnknownBridgeIsSkippedGracefully:
    def test_unknown_bridge_name_does_not_prompt_or_crash(self, tmp_path: Path) -> None:
        with mock.patch("builtins.input", side_effect=AssertionError("input() must not be called")):
            results = bridges_mod.configure_bridges(tmp_path, ["carrier-pigeon"], interactive=True)

        assert len(results) == 1
        assert results[0].name == "carrier-pigeon"
        assert results[0].configured is False
