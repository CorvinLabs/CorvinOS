"""Regression tests for corvinOS/installer/steps/dependencies.py::ensure_claude_login.

Bug investigated 2026-07-14: on a fresh install, voice summaries fell back to
reading the raw answer text word-for-word instead of the real learnings/
metaphors-style summary. Root cause traced to Claude Code being permanently
unauthenticated on fresh installs for two independent reasons:

  1. The instructed command was the stale ``claude login`` -- the current
     Claude Code CLI (2.x) only recognises ``claude auth login``; the old
     name silently falls through to being treated as a chat PROMPT instead
     of erroring, so nobody ever noticed it stopped working.
  2. Non-interactive installs (``curl | sh``, no TTY) unconditionally
     SKIPPED the login step altogether -- nothing ever attempted it.

These tests mock ``subprocess.Popen``/``find_claude_creds``/``shutil.which``
entirely; no real ``claude`` binary or OAuth flow is ever invoked.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import dependencies as deps_mod


# ── Already logged in ───────────────────────────────────────────────────────

def test_already_logged_in_short_circuits(monkeypatch, tmp_path):
    cred = tmp_path / ".credentials.json"
    cred.write_text("{}")
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: cred)
    popen_spy = mock.Mock()
    monkeypatch.setattr(deps_mod.subprocess, "Popen", popen_spy)

    assert deps_mod.ensure_claude_login(interactive=True) is True
    assert deps_mod.ensure_claude_login(interactive=False) is True
    popen_spy.assert_not_called()


def test_claude_binary_missing_skips_cleanly(monkeypatch):
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: None)
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: None)
    popen_spy = mock.Mock()
    monkeypatch.setattr(deps_mod.subprocess, "Popen", popen_spy)

    assert deps_mod.ensure_claude_login(interactive=False) is False
    popen_spy.assert_not_called()


# ── Interactive path: correct command, still waits on the user ─────────────

def test_interactive_prompts_with_correct_auth_login_command(monkeypatch, capsys):
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: None)
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda *a: "")

    result = deps_mod.ensure_claude_login(interactive=True)

    out = capsys.readouterr().out
    assert "claude auth login" in out
    assert "claude login" not in out.replace("claude auth login", "")
    assert result is False  # find_claude_creds still returns None after the prompt


def test_interactive_confirms_login_after_user_presses_enter(monkeypatch):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda *a: "")

    calls = {"n": 0}
    def _find():
        calls["n"] += 1
        return Path("/fake/creds.json") if calls["n"] > 1 else None
    monkeypatch.setattr(deps_mod, "find_claude_creds", _find)

    assert deps_mod.ensure_claude_login(interactive=True) is True


# ── Non-interactive path: drives login automatically instead of skipping ───

def test_noninteractive_launches_auth_login_not_stale_login_subcommand(monkeypatch):
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: None)
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(deps_mod.time, "sleep", lambda s: None)

    captured_cmd = []

    class _FakeProc:
        stdout = iter([])
        def poll(self):
            return 0  # exited immediately (simulates instant OAuth failure/skip)

    def _fake_popen(cmd, **kwargs):
        captured_cmd.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(deps_mod.subprocess, "Popen", _fake_popen)

    deps_mod.ensure_claude_login(interactive=False)

    assert captured_cmd == [["/usr/bin/claude", "auth", "login"]], (
        "must invoke 'claude auth login', not the stale 'claude login' "
        "(the current CLI no longer recognises the latter as a subcommand)"
    )


def test_noninteractive_polls_until_credentials_appear(monkeypatch):
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(deps_mod.time, "sleep", lambda s: None)

    calls = {"n": 0}
    def _find():
        calls["n"] += 1
        # credentials show up on the 3rd poll — simulates the user
        # completing OAuth in their browser while the installer waits
        return Path("/fake/creds.json") if calls["n"] >= 3 else None
    monkeypatch.setattr(deps_mod, "find_claude_creds", _find)

    class _FakeProc:
        stdout = iter([])
        def poll(self):
            return None  # still running (waiting on OAuth)

    monkeypatch.setattr(deps_mod.subprocess, "Popen", lambda cmd, **kw: _FakeProc())

    assert deps_mod.ensure_claude_login(interactive=False) is True


def test_noninteractive_gives_up_after_timeout_without_hanging(monkeypatch):
    """Regression guard: a login that's never completed must NOT hang the
    installer forever — bounded wait, same discipline as the imagegen
    MCP tool's own timeout fix."""
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: None)
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")

    # Fully-controlled fake clock, starting at 0 -- real monotonic() can
    # already be a large value (system uptime), so nudging it "into the
    # future" by a fixed constant risks landing BEFORE the real deadline
    # and spinning forever instead of exiting.
    clock = {"t": 0.0}
    monkeypatch.setattr(deps_mod.time, "monotonic", lambda: clock["t"])
    sleep_calls = {"n": 0}
    def _fake_sleep(s):
        sleep_calls["n"] += 1
        clock["t"] += deps_mod._LOGIN_POLL_INTERVAL_S
    monkeypatch.setattr(deps_mod.time, "sleep", _fake_sleep)

    class _FakeProc:
        stdout = iter([])
        def poll(self):
            return None  # process never exits — simulates a stuck/never-completed OAuth wait

    monkeypatch.setattr(deps_mod.subprocess, "Popen", lambda cmd, **kw: _FakeProc())

    result = deps_mod.ensure_claude_login(interactive=False)

    assert result is False
    assert sleep_calls["n"] < 100, "polling loop must terminate, not spin forever"


def test_noninteractive_streams_popen_output_without_reading_stdin(monkeypatch):
    """The non-interactive path must never call input() — that would hang
    a piped install with no TTY (the original bug this whole flow exists
    to fix)."""
    monkeypatch.setattr(deps_mod, "find_claude_creds", lambda: None)
    monkeypatch.setattr(deps_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(deps_mod.time, "sleep", lambda s: None)

    def _boom_input(*a, **k):
        raise AssertionError("non-interactive path must not call input()")
    monkeypatch.setattr("builtins.input", _boom_input)

    class _FakeProc:
        stdout = iter(["Open this URL: https://claude.ai/oauth/xyz\n"])
        def poll(self):
            return 0
    monkeypatch.setattr(deps_mod.subprocess, "Popen", lambda cmd, **kw: _FakeProc())

    deps_mod.ensure_claude_login(interactive=False)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
