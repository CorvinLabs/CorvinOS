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


_DUMPER = (
    "import json, os, sys\n"
    "sp = next((a for i, a in enumerate(sys.argv) if sys.argv[i-1].endswith('-prompt-file')), None)\n"
    "json.dump({'argv': sys.argv[2:], 'stdin': sys.stdin.read(),\n"
    "           'sysfile': open(sp, encoding='utf-8').read() if sp else None},\n"
    "          open(os.path.join(sys.argv[1], 'out.json'), 'w', encoding='utf-8'))\n"
)


def test_shim_text_transport_identity_for_exe_and_plain_text() -> None:
    for argv in (["C:/x/claude.exe", "-p", "a\nb"], ["C:/x/claude.cmd", "-p", "one line"],
                 ["C:/x/npm.cmd", "-p", "a\nb"]):
        out, fh, cleanup = _wincompat.shim_text_transport(argv, None)
        assert out is argv and fh is None and cleanup is None


@pytest.mark.skipif(sys.platform != "win32", reason="real cmd.exe round-trip")
@pytest.mark.parametrize("positional_style", ["after_dash_p", "after_sentinel"])
def test_multiline_prompt_reaches_claude_cmd_shim_whole(tmp_path, positional_style) -> None:
    """cmd.exe cuts a command line at the first newline: the A2A framed prompt
    arrived as just "User input:". The Popen backstop moves it to stdin."""
    import os
    _wincompat.install_batch_argv_quoting()
    (tmp_path / "d.py").write_text(_DUMPER, encoding="utf-8")
    shim = tmp_path / "claude.cmd"
    shim.write_text(f'@"{sys.executable}" "{tmp_path / "d.py"}" "{tmp_path}" %*\r\n', encoding="utf-8")
    prompt = "User input:\nrow2 %PATH% & echo PWN\n\"q\" ^ | >x\n" + "z" * 9000
    sysp = "S1\nS2 %USERPROFILE%"
    argv = ([str(shim), "-p", prompt, "--append-system-prompt", sysp] if positional_style == "after_dash_p"
            else [str(shim), "-p", "--append-system-prompt", sysp, "--", prompt])
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert got["stdin"].replace("\r\n", "\n") == prompt
    assert prompt not in got["argv"] and "--" not in got["argv"]
    assert got["sysfile"] == sysp
    assert not (tmp_path / "PWN").exists()
    import gc
    gc.collect()
