"""install.ps1 / the generated supervisor must never let `uv tool
upgrade|install ... corvinos` run while a process from the SAME tool env is
still holding files open (2026-08-04, INST-14 + INST-15).

Live-reported TWICE on real Windows installs: `corvin serve` / `corvin-serve`
crashed with `ModuleNotFoundError: No module named 'ops'` even AFTER shipping
0.10.110 (which added the missing `ops/__init__.py` / `ops/launcher/__init__.py`
files -- a real, necessary fix, but not sufficient on its own).

INST-14 (this file's original guard): a defensive fix for install.ps1's OWN
`uv tool install --force --refresh` racing an in-flight `uv.exe` process for
corvinos (e.g. a second install.ps1 run, or the supervisor's own upgrade job
still mid-flight). Kept as a real, cheap safety net -- but ground-truth
diagnosis of the SECOND live report (below) found the actual mechanism was
simpler and more direct than a two-`uv.exe`-processes race.

INST-15 (the confirmed root cause, added after the operator supplied a real
`uv tool list -v` trace): `uv tool upgrade ... --reinstall-package` UNINSTALLS
corvinos (deletes its tool-env files) before reinstalling. On the real
machine that delete step failed with "The process cannot access the file
because it is being used by another process" (os error 32) -- two orphaned
`adapter.py` processes (`bridge_manager.ensure_adapter_detached()`, spawned
from the SAME tool env: `<tool-env>\\Scripts\\python.exe ... adapter.py`)
were still holding files open under
`corvin_console\\_vendor\\operator\\bridges\\shared\\`. uv's uninstall step
does not roll back on partial failure: corvinos' own files (including the
`ops` package every entry point imports) were gone while its 73 dependencies
remained -- `uv tool list -v` then reported "Failed find package 'corvinos'
in tool environment" and every `corvin*` command died with
`ModuleNotFoundError: No module named 'ops'`. Neither install.ps1's own
pre-install cleanup NOR the supervisor's auto-update block ever killed
adapter.py before this session -- fixed by (a) adding `adapter\\.py` to
install.ps1's own pre-install cleanup pattern, and (b) adding an equivalent
cleanup immediately before the supervisor's OWN
`uv tool upgrade --reinstall-package` call, since THAT is the code path that
actually failed live (it runs unattended on every logon with no prior
cleanup at all).

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


class AdapterPyPreinstallCleanupTests(unittest.TestCase):
    """INST-15: install.ps1's own pre-install cleanup must also kill
    adapter.py -- the process that actually held the lock in the live
    incident."""

    def setUp(self) -> None:
        self.src = _INSTALL_PS1.read_text(encoding="utf-8")

    def test_preinstall_cleanup_pattern_includes_adapter_py(self) -> None:
        self.assertIn(
            'corvinos-serve|corvin-serve|corvin_console|corvin_gateway|adapter\\.py',
            self.src,
        )


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
class AdapterPyRealFilterExecutionTests(unittest.TestCase):
    """Same real-pwsh-execution discipline as RealFilterExecutionTests
    above, but for the broadened preinstall-cleanup predicate: proves it
    actually matches an adapter.py-shaped process (Name python.exe, as
    bridge_manager.ensure_adapter_detached() spawns it via sys.executable)
    and does not collaterally match an unrelated editor with a corvin path
    in its argv."""

    def _run_filter(self, processes_ps_literal: str) -> str:
        script = f"""
$fakeProcs = {processes_ps_literal}
$matched = $fakeProcs | Where-Object {{
    $_.CommandLine -and
    $_.CommandLine -match "corvinos-serve|corvin-serve|corvin_console|corvin_gateway|adapter\\.py" -and
    $_.Name -match "^python|^corvin"
}}
$matched | ForEach-Object {{ Write-Output $_.Tag }}
"""
        path = Path("/tmp/corvin_adapter_cleanup_test.ps1")
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

    def test_orphaned_adapter_process_is_matched(self) -> None:
        procs = (
            '@('
            '[PSCustomObject]@{ Tag = "MATCH_adapter"; Name = "python.exe"; '
            'CommandLine = "C:\\Users\\sjurk\\AppData\\Roaming\\uv\\tools\\corvinos\\Scripts\\python.exe '
            'C:\\Users\\sjurk\\AppData\\Roaming\\uv\\tools\\corvinos\\Lib\\site-packages\\corvin_console\\_vendor'
            '\\operator\\bridges\\shared\\adapter.py" },'
            '[PSCustomObject]@{ Tag = "NOMATCH_editor"; Name = "Code.exe"; '
            'CommandLine = "Code.exe C:\\projects\\corvinos\\adapter.py" }'
            ')'
        )
        out = self._run_filter(procs)
        self.assertIn("MATCH_adapter", out)
        self.assertNotIn("NOMATCH_editor", out)


class SupervisorReinstallCleanupTests(unittest.TestCase):
    """INST-15's actual fix: the supervisor's OWN auto-update block (the
    code path that failed live -- it runs unattended on every logon with no
    prior cleanup) must clear locking processes before
    `uv tool upgrade --reinstall-package`."""

    def setUp(self) -> None:
        self.src = _INSTALL_PS1.read_text(encoding="utf-8")

    def test_supervisor_cleanup_block_present(self) -> None:
        self.assertIn(
            '`$_.CommandLine -match "corvinos-serve|corvin-serve|corvin_console|corvin_gateway|adapter\\.py" -and',
            self.src,
        )

    def test_supervisor_cleanup_runs_before_reinstall_package(self) -> None:
        cleanup_idx = self.src.index(
            'Write-Log "auto-update: clearing any process still holding tool-env files open"'
        )
        reinstall_idx = self.src.index(
            'Write-Log "auto-update: uv tool upgrade corvinos --reinstall-package corvinos"'
        )
        self.assertLess(cleanup_idx, reinstall_idx,
                         "the supervisor's process cleanup must run BEFORE --reinstall-package")

    def test_supervisor_cleanup_is_inside_the_uv_available_branch(self) -> None:
        # Must not run (and Stop-Process) when $uv resolved to nothing --
        # keeps this block scoped to the exact moment a reinstall is about
        # to happen, same as the pre-existing --reinstall-package call it
        # guards.
        uv_if_idx = self.src.index("if (`$uv) {")
        cleanup_idx = self.src.index(
            'Write-Log "auto-update: clearing any process still holding tool-env files open"'
        )
        reinstall_idx = self.src.index(
            'Write-Log "auto-update: uv tool upgrade corvinos --reinstall-package corvinos"'
        )
        self.assertLess(uv_if_idx, cleanup_idx)
        self.assertLess(cleanup_idx, reinstall_idx)


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
class SupervisorGeneratedScriptStillParsesTests(unittest.TestCase):
    """The new cleanup block sits inside install.ps1's OWN here-string that
    generates corvin-supervisor.ps1 -- must not break the generated file's
    own validity. Runs the real generation logic (Install-CorvinAutostart's
    heredoc) through a harness mirroring install.ps1's actual interpolation,
    then parses the RESULT with a real PowerShell parser."""

    def test_generated_supervisor_still_parses_cleanly(self) -> None:
        harness = r"""
$CorvinHomeEscaped = "C:\Users\tester\.corvin"
$ServeCmdEscaped = "C:\Users\tester\AppData\Roaming\uv\tools\corvinos\Scripts\corvin-serve.exe"
$SupervisorEscaped = "C:\Users\tester\.corvin\bin\corvin-supervisor.ps1"
$OutPath = "/tmp/corvin_supervisor_generated_test.ps1"
"""
        # Extract the heredoc body verbatim from install.ps1 (between the
        # opening @" line and the closing "@ | Set-Content), same technique
        # test_windows_supervisor_parity.py's _generated_supervisor_block()
        # uses, but generated live through pwsh instead of Python string
        # slicing so the interpolation is real, not simulated.
        src = _INSTALL_PS1.read_text(encoding="utf-8")
        start = src.index('@"\n# Auto-generated by install.ps1')
        end = src.index('"@ | Set-Content', start) + len('"@')
        heredoc = src[start:end]
        full_script = harness + f'{heredoc} | Set-Content -Path $OutPath -Encoding UTF8\n'

        harness_path = Path("/tmp/corvin_supervisor_gen_harness.ps1")
        harness_path.write_text(full_script, encoding="utf-8")
        out_path = Path("/tmp/corvin_supervisor_generated_test.ps1")
        try:
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(harness_path)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(out_path.exists(), "harness did not write the generated supervisor script")

            parse_check = f"""
$errs = $null
[System.Management.Automation.Language.Parser]::ParseFile('{out_path}', [ref]$null, [ref]$errs) | Out-Null
if ($errs.Count -gt 0) {{ $errs | ForEach-Object {{ Write-Host $_.Message }}; exit 1 }}
"""
            parse_result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", parse_check],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(parse_result.returncode, 0, parse_result.stdout + parse_result.stderr)

            content = out_path.read_text(encoding="utf-8")
            self.assertIn("adapter\\.py", content)
            self.assertIn("clearing any process still holding tool-env files open", content)
        finally:
            harness_path.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
