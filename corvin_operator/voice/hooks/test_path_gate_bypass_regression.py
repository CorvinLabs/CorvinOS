#!/usr/bin/env python3
"""Regression tests for the four L10 path-gate bypasses fixed 2026-07-08.

Each was empirically confirmed to ALLOW a write to a protected path before the
fix (adversarial pentest). They must stay DENIED, and benign look-alikes must
stay ALLOWED (no over-block).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import path_gate as pg  # type: ignore  # noqa: E402


def _chk(cmd: str) -> bool:
    allow, _ = pg.check({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return allow


# A protected path on any install (the voice secret vault).
_VAULT = "~/.config/corvin-voice/secrets.json"


def test_bypass_D_newline_separator_denied():
    # Newline is a real bash separator; the second line must be inspected.
    assert _chk(f"echo ok\nrm -f {_VAULT}") is False
    assert _chk(f"echo ok\rrm -f {_VAULT}") is False


def test_bypass_C_quoted_redirect_denied():
    assert _chk(f'echo poison > "{_VAULT}"') is False
    assert _chk(f"echo poison > '{_VAULT}'") is False


def test_bypass_A_cd_relative_write_denied():
    # cd into a protected tree, then a relative write via a non-destructive
    # builtin (`:`/printf/tee) + redirect.
    assert _chk("cd ~/.config/corvin-voice && : > secrets.json") is False
    assert _chk("cd ~/.config/corvin-voice && printf x > secrets.json") is False
    assert _chk("cd ~/.config/corvin-voice && echo x | tee secrets.json") is False
    # Compute the corvin home at call time — other tests mutate CORVIN_HOME, and
    # the gate resolves it live, so a module-load snapshot would drift.
    home = str(pg._corvin_home())
    assert _chk(f"cd {home}/global/forge && : > audit.jsonl") is False


def test_bypass_B_inline_interpreter_denied():
    assert _chk(f'python3 -c "open(\\"{_VAULT}\\", mode=\\"w\\").write(1)"') is False
    assert _chk(f'perl -e "open(F,\\">{_VAULT}\\")"') is False


def test_benign_not_overblocked():
    assert _chk("echo hello world") is True
    assert _chk("python3 -c \"print(1+1)\"") is True
    assert _chk("git status") is True
    assert _chk("cd ~/.config/corvin-voice && cat config.json") is True
    assert _chk("cd ~/.config/corvin-voice && echo x > /tmp/out.txt") is True
    assert _chk("cd ~/projects/x && rm -rf dist") is True


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS: {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL: {name}: {e}")
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
