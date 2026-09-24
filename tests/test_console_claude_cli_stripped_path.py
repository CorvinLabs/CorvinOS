"""The console must find the claude CLI under a service manager's stripped PATH.

corvin-webui.service (systemd) and the launchd agent start the console with a
minimal PATH that lacks ``~/.local/bin`` — where the native Claude Code
installer puts the CLI. ``chat_runtime._claude_binary`` used to read only
``CORVIN_CLAUDE_BIN`` and ``PATH``, so every web-chat turn answered "The
Claude Code engine is selected, but the `claude` CLI was not found" on a
machine where ``claude`` works in every shell (2026-09-24).

Probed in a fresh interpreter with a scrubbed environment and a fake HOME, so
neither the developer's real PATH nor an earlier in-process import can make
the test pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVICE_PYTHONPATH = [
    "core/console", "core/gateway", "core/license", "core/compliance",
    "corvin_operator/forge", "corvin_operator/skill-forge", "core/plugins",
]
STRIPPED_PATH = "/usr/local/sbin:/usr/sbin:/sbin"

PROBE = r"""
import json, shutil
before = shutil.which("claude")
from corvin_console import chat_runtime as c
print(json.dumps({
    "before": before,
    "binary": c._claude_binary(),
    "which_after": shutil.which("claude"),
    "message": c._engine_unavailable_message("claude_code"),
}))
"""


def _probe(home: Path) -> dict:
    env = {
        "HOME": str(home),
        "PATH": STRIPPED_PATH,
        "CORVIN_HOME": str(home / ".corvin"),
        "PYTHONPATH": os.pathsep.join(str(REPO / p) for p in SERVICE_PYTHONPATH),
    }
    out = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, cwd=str(REPO),
        capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def _fake_cli(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho 2.0.0\n")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX service PATH shape")
@pytest.mark.parametrize("rel", [
    ".local/bin/claude",                        # native installer
    ".npm-global/bin/claude",                   # npm with a user prefix
    ".nvm/versions/node/v22.3.0/bin/claude",    # npm under nvm
])
def test_console_resolves_cli_off_service_path(tmp_path: Path, rel: str) -> None:
    fake = _fake_cli(tmp_path / rel)
    got = _probe(tmp_path)
    # Positive control: the stripped PATH really cannot see the CLI.
    assert got["before"] is None
    assert got["binary"] == str(fake)
    assert got["which_after"] == str(fake)  # harden_path repaired PATH
    assert got["message"] is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX service PATH shape")
def test_console_still_reports_a_genuinely_missing_cli(tmp_path: Path) -> None:
    got = _probe(tmp_path)
    assert got["before"] is None and got["which_after"] is None
    assert got["message"] and "was not found" in got["message"]
