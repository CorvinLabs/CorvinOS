"""bridge.ps1's `install-autostart <channel>` must never let a failed
Console-task registration block the per-channel Bridge task (INST-16,
2026-08-04).

Live-reported: every configured bridge (discord, whatsapp) logged
`windows autostart registration for {channel} exited 1` on EVERY
`corvin-serve` start on Windows, with the real error visible in the log:
`Register-ScheduledTask : Access is denied.` at bridge.ps1:286 -- the
Console task registration, not the bridge one.

Root cause: bridge.ps1 sets `$ErrorActionPreference = "Stop"` at file scope,
so ANY cmdlet error (including Register-ScheduledTask's) becomes a
TERMINATING error. The `install-autostart` case block registered
"CorvinOS-Console" FIRST, unconditionally, before "CorvinOS-Bridge-$Bridge"
-- so a Console-task failure (most likely a stale/foreign-ACL task left
behind by install.ps1's OWN separate registration of the same task name)
aborted the whole command before the per-channel Bridge task was ever
attempted. bridge_manager.ensure_windows_autostart() calls this once per
CONFIGURED channel on every boot, so this repeated on every single startup,
and no bridge ever got its own restart-on-reboot Scheduled Task -- even
though the bridge daemon itself was already running fine (this command is
strictly best-effort, called after the daemon is confirmed up).

Fixed two ways, both covered here:
  1. Each of the two registrations gets its own try/catch -- independent
     failure domains.
  2. $env:CORVIN_AUTOSTART_BRIDGE_ONLY=1 (set only by
     bridge_manager.ensure_windows_autostart(), never by a human) skips the
     redundant Console registration entirely on the automatic path.

Run: python3 -m pytest tests/test_bridge_ps1_autostart_isolation.py
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BRIDGE_PS1 = _REPO / "operator" / "bridges" / "bridge.ps1"
_BRIDGE_MANAGER = _REPO / "operator" / "bridges" / "bridge_manager.py"


class StaticPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _BRIDGE_PS1.read_text(encoding="utf-8")

    def test_console_registration_wrapped_in_try_catch(self) -> None:
        idx = self.src.index('Install-AutostartTask -TaskName "CorvinOS-Console" -TargetArg "console"')
        head = self.src[max(0, idx - 200):idx]
        self.assertIn("try {", head)

    def test_bridge_registration_wrapped_in_try_catch_and_exits_nonzero_on_failure(self) -> None:
        idx = self.src.index('Install-AutostartTask -TaskName "CorvinOS-Bridge-$Bridge"')
        tail = self.src[idx:idx + 300]
        self.assertIn("catch {", tail)
        self.assertIn("exit 1", tail)

    def test_bridge_registration_runs_regardless_of_console_outcome(self) -> None:
        # The bridge Install-AutostartTask call must not be nested inside
        # the console's try/catch (which would make it unreachable if a
        # `throw`/`return` inside the catch ever got added later).
        console_idx = self.src.index('Install-AutostartTask -TaskName "CorvinOS-Console"')
        bridge_idx = self.src.index('Install-AutostartTask -TaskName "CorvinOS-Bridge-$Bridge"')
        between = self.src[console_idx:bridge_idx]
        # The console try/catch must have fully closed (its catch block's
        # closing brace) before the bridge block starts.
        self.assertIn("}\n        }\n        try {", between.replace("\r\n", "\n"))

    def test_bridge_only_env_var_gates_console_registration(self) -> None:
        self.assertIn('$env:CORVIN_AUTOSTART_BRIDGE_ONLY -ne "1"', self.src)


class BridgeManagerSetsIsolationEnvVarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _BRIDGE_MANAGER.read_text(encoding="utf-8")

    def test_ensure_windows_autostart_sets_bridge_only_env_var(self) -> None:
        idx = self.src.index("def ensure_windows_autostart")
        next_def = self.src.index("\ndef ", idx + 1)
        body = self.src[idx:next_def]
        self.assertIn('env["CORVIN_AUTOSTART_BRIDGE_ONLY"] = "1"', body)
        self.assertIn("env=env", body)


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
class RealExecutionIsolationTests(unittest.TestCase):
    """Extracts the actual `install-autostart` case body verbatim and runs
    it through real pwsh, with the Windows-only Scheduled-Task cmdlets
    replaced by fakes that simulate the live incident (Console task throws
    Access Denied, Bridge task would otherwise succeed) -- proves the fix
    with real control flow, not just text presence."""

    def _extract_case_body(self) -> str:
        src = _BRIDGE_PS1.read_text(encoding="utf-8")
        start = src.index('Write-Host "Installing CorvinOS autostart (Windows Task Scheduler)..."')
        end = src.index('Write-Host "Undo with: .\\bridge.ps1 uninstall-autostart" -ForegroundColor DarkGray')
        end = src.index("\n", end) + 1
        return src[start:end]

    def _run(self, console_fails: bool, bridge_only: bool) -> subprocess.CompletedProcess:
        fake_register = """
$Global:RegisterCalls = @()
function Register-ScheduledTask {
    param([string]$TaskName, $Action, $Trigger, $Settings, $RunLevel, [string]$Description)
    $Global:RegisterCalls += $TaskName
    if ($TaskName -eq "CorvinOS-Console" -and $env:FAKE_CONSOLE_FAILS -eq "1") {
        throw "Access is denied."
    }
    return $null
}
function Unregister-ScheduledTask { param([string]$TaskName, [switch]$Confirm, $ErrorAction) }
function Start-ScheduledTask { param([string]$TaskName) }
function New-ScheduledTaskAction { param($Execute, $Argument) return $null }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn) return $null }
function New-ScheduledTaskSettingsSet {
    param([switch]$Hidden, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries,
          $ExecutionTimeLimit, $RestartCount, $RestartInterval, $MultipleInstances)
    return $null
}
"""
        case_body = self._extract_case_body()
        script = f"""
$ErrorActionPreference = "Stop"
{fake_register}
$ScriptDir = "/tmp/corvin-bridge-ps1-test"
New-Item -ItemType Directory -Force -Path "$ScriptDir/shared" | Out-Null
Set-Content -Path "$ScriptDir/shared/corvin-supervisor.ps1" -Value "# fake" -Force
$Bridge = "discord"
$env:CORVIN_HOME = "/tmp/corvin-test-home"
New-Item -ItemType Directory -Force -Path $env:CORVIN_HOME | Out-Null

{case_body}
Write-Output ("REGISTERED: " + ($Global:RegisterCalls -join ","))
"""
        path = Path("/tmp/corvin_bridge_autostart_isolation_test.ps1")
        path.write_text(script, encoding="utf-8")
        pwsh_path = shutil.which("pwsh")
        pwsh_dir = str(Path(pwsh_path).parent) if pwsh_path else ""
        env = {"PATH": f"{pwsh_dir}:/usr/bin:/bin"}
        if console_fails:
            env["FAKE_CONSOLE_FAILS"] = "1"
        if bridge_only:
            env["CORVIN_AUTOSTART_BRIDGE_ONLY"] = "1"
        try:
            return subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(path)],
                capture_output=True, text=True, timeout=15, env=env,
            )
        finally:
            path.unlink(missing_ok=True)

    def test_console_failure_does_not_block_bridge_registration(self) -> None:
        result = self._run(console_fails=True, bridge_only=False)
        self.assertIn("CorvinOS-Bridge-discord", result.stdout,
                       f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_console_failure_both_registered(self) -> None:
        result = self._run(console_fails=False, bridge_only=False)
        self.assertIn("CorvinOS-Console", result.stdout)
        self.assertIn("CorvinOS-Bridge-discord", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_bridge_only_env_var_skips_console_entirely(self) -> None:
        result = self._run(console_fails=True, bridge_only=True)
        self.assertNotIn("CorvinOS-Console", result.stdout)
        self.assertIn("CorvinOS-Bridge-discord", result.stdout)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
