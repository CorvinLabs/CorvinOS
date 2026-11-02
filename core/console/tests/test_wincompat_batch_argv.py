"""BatBadBut guard: list-argv spawns of a .cmd/.bat get cmd.exe-safe quoting.

On Windows CreateProcess runs a batch file as ``cmd.exe /c "<list2cmdline>"``;
list2cmdline's ``\\"`` is a quote toggle for cmd, so an argument such as
``x" & echo PWN & "y`` executed ``echo PWN`` (measured). Every
``[resolve_claude_bin(), "-p", prompt]`` spawn hit this for an npm-installed
``claude.cmd``. ``corvin_core._wincompat`` rewrites such argv at Popen level.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from corvin_core import _wincompat


def test_batch_safe_command_leaves_non_batch_argv_alone() -> None:
    argv = ["C:/x/claude.exe", "-p", 'a" & b']
    assert _wincompat.batch_safe_command(argv) is argv
    assert _wincompat.batch_safe_command("cmd /c x") == "cmd /c x"


def test_batch_safe_command_matches_win_shim_builder() -> None:
    from pathlib import Path
    shared = Path(__file__).resolve().parents[3] / "corvin_operator" / "bridges" / "shared" / "agents"
    sys.path.insert(0, str(shared))
    try:
        import _win_shim  # type: ignore
    finally:
        sys.path.remove(str(shared))
    argv = ["C:\\n p\\claude.cmd", "-p", 'x" & y', "", "dir\\"]
    assert _wincompat.batch_safe_command(argv) == _win_shim.build_cmd_c_line(argv)


@pytest.mark.skipif(sys.platform != "win32", reason="real cmd.exe round-trip")
def test_list_argv_batch_spawn_has_no_breakout(tmp_path) -> None:
    _wincompat.install_batch_argv_quoting()
    shim = tmp_path / "with space" / "claude.cmd"
    shim.parent.mkdir()
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" -c "import sys,json;'
        f'print(json.dumps(sys.argv[1:]))" %*\r\n', encoding="ascii")
    args = ["-p", 'x" & echo PWN & "y', "--tools", "", "a|b<c>^d"]
    r = subprocess.run([str(shim), *args], capture_output=True, text=True,
                       timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == args, r.stdout
