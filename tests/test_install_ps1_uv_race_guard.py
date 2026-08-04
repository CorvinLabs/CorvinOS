"""install.ps1 must kill an in-flight `uv.exe` process for corvinos before
starting its own `uv tool install` (2026-08-04, INST-14).

Live-reported TWICE on real Windows installs: `corvin serve` / `corvin-serve`
crashed with `ModuleNotFoundError: No module named 'ops'` even AFTER shipping
0.10.110 (which added the missing `ops/__init__.py` / `ops/launcher/__init__.py`
files -- a real, necessary fix, but not sufficient on its own). The second
report's `uv tool list -v` showed `Failed find package 'corvinos' in tool
environment` -- a genuinely CORRUPTED venv, not just a namespace-package
resolution quirk.

Root cause: the generated `corvin-supervisor.ps1` (Install-CorvinAutostart)
runs `uv tool upgrade corvinos --reinstall-package corvinos` in a background
Start-Job on EVERY logon, with up to a 120s window, before its restart loop.
install.ps1's own pre-install cleanup (INST-2) stops/disables the
CorvinOS-Console scheduled task and kills corvin-serve.exe-ish processes, but
never touches `uv.exe` -- so re-running install.ps1 shortly after a
logon/reboot can start `uv tool install --force --refresh` while the
supervisor's own `uv tool upgrade` is STILL WRITING to the exact same
`%APPDATA%/uv/tools/corvinos` directory, corrupting its metadata. Fixed by
killing any in-flight `uv.exe` process whose command line mentions
`corvinos` immediately before install.ps1 starts its own install.

Run: python3 -m pytest tests/test_install_ps1_uv_race_guard.py
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INSTALL_PS1 = _REPO / "install.ps1"


class StaticPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _INSTALL_PS1.read_text(encoding="utf-8")

    def test_uv_exe_race_guard_block_present(self) -> None:
        self.assertIn('$_.Name -eq "uv.exe" -and $_.CommandLine -match "corvinos"', self.src)

    def test_race_guard_runs_before_the_install_call(self) -> None:
        guard_idx = self.src.index('$_.Name -eq "uv.exe"')
        install_idx = self.src.index('uv tool install --force --refresh "$Package[browser]"')
        self.assertLess(guard_idx, install_idx,
                         "the uv.exe race guard must run BEFORE the real install call")

    def test_race_guard_uses_force_kill(self) -> None:
        # Same discipline as the pre-existing corvin-serve cleanup right
        # above it -- must not hang the installer waiting on a stuck process.
        idx = self.src.index('$_.Name -eq "uv.exe" -and $_.CommandLine -match "corvinos"')
        tail = self.src[idx:idx + 400]
        self.assertIn("Stop-Process -Id $_.ProcessId -Force", tail)


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
class RealFilterExecutionTests(unittest.TestCase):
    """Runs the ACTUAL Where-Object predicate (extracted verbatim from
    install.ps1) against synthetic Win32_Process-shaped objects through a
    real PowerShell parser/executor -- proves the filter selects the
    supervisor's background upgrade job and nothing else."""

    def _run_filter(self, processes_ps_literal: str) -> str:
        script = f"""
$fakeProcs = {processes_ps_literal}
$matched = $fakeProcs | Where-Object {{
    $_.Name -eq "uv.exe" -and $_.CommandLine -match "corvinos"
}}
$matched | ForEach-Object {{ Write-Output $_.Tag }}
"""
        path = Path("/tmp/corvin_uv_race_guard_test.ps1")
        path.write_text(script, encoding="utf-8")
        try:
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        finally:
            path.unlink(missing_ok=True)

    def test_supervisor_background_upgrade_job_is_matched(self) -> None:
        procs = (
            '@('
            '[PSCustomObject]@{ Tag = "MATCH_upgrade"; Name = "uv.exe"; '
            'CommandLine = "C:\\Users\\sjurk\\.local\\bin\\uv.exe tool upgrade corvinos --reinstall-package corvinos" },'
            '[PSCustomObject]@{ Tag = "NOMATCH_other_tool"; Name = "uv.exe"; '
            'CommandLine = "C:\\Users\\sjurk\\.local\\bin\\uv.exe tool install --force ruff" },'
            '[PSCustomObject]@{ Tag = "NOMATCH_wrong_name"; Name = "python.exe"; '
            'CommandLine = "python.exe -m corvinos" },'
            '[PSCustomObject]@{ Tag = "NOMATCH_editor"; Name = "Code.exe"; '
            'CommandLine = "Code.exe C:\\projects\\corvinos" }'
            ')'
        )
        out = self._run_filter(procs)
        self.assertIn("MATCH_upgrade", out)
        self.assertNotIn("NOMATCH_other_tool", out)
        self.assertNotIn("NOMATCH_wrong_name", out)
        self.assertNotIn("NOMATCH_editor", out)

    def test_concurrent_install_job_is_also_matched(self) -> None:
        """A second `uv tool install` (e.g. a double-clicked install.ps1)
        racing the first must be caught the same way as the supervisor's
        upgrade job -- the guard is deliberately install-vs-upgrade agnostic."""
        procs = (
            '@('
            '[PSCustomObject]@{ Tag = "MATCH_install"; Name = "uv.exe"; '
            'CommandLine = "uv.exe tool install --force --refresh corvinos[browser]" }'
            ')'
        )
        out = self._run_filter(procs)
        self.assertIn("MATCH_install", out)


if __name__ == "__main__":
    unittest.main()
