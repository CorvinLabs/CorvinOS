"""2026-08-03, reported live: after a fresh Windows install, starting a
bridge from the console still popped a visible console window -- despite
0.10.91, 0.10.95 and 850e50f each having already fixed a DIFFERENT spawn
site in this codebase for this exact symptom. Root cause: bridge_manager.py
alone had 10+ independent, hand-rolled subprocess.run() call sites (node
--version, winget/npm install, tasklist, wmic, PowerShell CIM, taskkill,
systemctl, ps, pgrep) -- each needing its own CREATE_NO_WINDOW, so each
prior fix round closed one call site and left the next one undiscovered.
bridge_manager.py's caller (the web console backend) has no console of its
own (started detached/hidden), so spawning ANY console-subsystem child
without CREATE_NO_WINDOW makes Windows allocate a brand-new, visible one.

Fix: every plain subprocess.run() call in bridge_manager.py now goes
through a single local _run() wrapper that applies
agents._win_shim.no_console_window_flags() by default -- the flag becomes
structural (one place to get right) instead of one more thing each new
call site has to remember. This test pins that: (1) _run() actually
applies the flag and lets an explicit caller override it, and (2) no bare
subprocess.run() call re-appears in the module outside _run()'s own
definition -- a static drift guard, since the whole point of this fix is
that the NEXT call site added to this file must not get to skip it.

Run: python3 operator/bridges/tests/test_bridge_manager_no_console_window.py
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator" / "bridges"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

import bridge_manager  # noqa: E402

_SRC_PATH = _REPO / "operator" / "bridges" / "bridge_manager.py"


def _bare_subprocess_run_lines() -> list[int]:
    """Every line number where `subprocess.run(` appears OUTSIDE _run()'s own
    body, parsed via the AST (not a grep) so docstrings/comments can't produce
    a false positive and a genuine new call site can't hide inside a string."""
    tree = ast.parse(_SRC_PATH.read_text(encoding="utf-8"), filename=str(_SRC_PATH))
    run_def_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run":
            run_def_lines = set(range(node.lineno, node.end_lineno + 1))
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.lineno not in run_def_lines
        ):
            offenders.append(node.lineno)
    return offenders


class TestNoBareSubprocessRunOutsideWrapper(unittest.TestCase):
    def test_every_subprocess_run_call_goes_through_run_wrapper(self):
        offenders = _bare_subprocess_run_lines()
        self.assertEqual(
            offenders, [],
            f"bridge_manager.py has bare subprocess.run() call(s) at line(s) "
            f"{offenders} bypassing _run() -- these will flash a visible "
            f"console window on Windows (the exact bug this test guards "
            f"against). Route them through _run() instead.",
        )


class TestRunWrapper(unittest.TestCase):
    def test_run_applies_no_console_window_flags_by_default(self):
        with mock.patch.object(bridge_manager, "no_console_window_flags", return_value=0x08000000), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            bridge_manager._run(["echo", "hi"])
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs.get("creationflags"), 0x08000000)

    def test_run_lets_caller_override_creationflags(self):
        """start_fg()-style callers that need CREATE_NEW_PROCESS_GROUP (not
        just no-window) must still be able to pass their own value."""
        with mock.patch.object(bridge_manager, "no_console_window_flags", return_value=0x08000000), \
             mock.patch.object(bridge_manager.subprocess, "run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            bridge_manager._run(["echo", "hi"], creationflags=0x00000200)
        self.assertEqual(run_mock.call_args.kwargs.get("creationflags"), 0x00000200)

    def test_run_is_a_noop_flag_on_posix(self):
        """Real (unmocked) no_console_window_flags() must return 0 on this
        (POSIX) test runner, so creationflags=0 is passed -- subprocess.run
        raises ValueError if creationflags != 0 on a non-Windows platform,
        so this also proves _run() never breaks a real POSIX subprocess."""
        result = bridge_manager._run(
            [sys.executable, "-c", "print('ok')"], capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
