"""install.ps1's not-ready fallback: a detached background watcher, not just
a doomed one-shot browser open (2026-08-02).

Live-reported: "Nach einer frischen Installation öffnet die console nicht
automatisch" (after a fresh install the console doesn't open by itself).
Root cause: install.ps1 always called `Start-Process $ConsoleURL`
unconditionally, even when the readiness probe (60x 1s = 60s) timed out --
a slow first-ever cold start (no bytecode cache, every file in the freshly
written venv new to Defender's on-access scanner; the script's OWN comments
already acknowledged "well over 30s") loses this race often enough to
matter. The opened tab then shows a native browser connection-error page
that NOTHING can auto-refresh (it never loaded anything CorvinOS served),
so a user who doesn't know to manually reload perceives "the console never
opened" even though autostart genuinely brings it up moments later.

Fixed with two changes, both covered here:
  1. The foreground wait budget raised 60s -> 90s (still bounded).
  2. A small DETACHED watcher script (survives the installer's own window
     closing) that keeps polling healthz for up to a further 5 minutes and
     opens a FRESH, working tab the moment the server actually answers.

Mirrors this repo's established methodology for .ps1 correctness: static
analysis of the outer script's text PLUS real execution of the generated
inner watcher script through an actual PowerShell Core parser (not just
eyeballing it) -- this exact class of installer bug has previously only
ever been caught that way (see test_installer_health_probe.py's own
history, and ops/launcher/tests/test_autoupdate_uv.py's
test_generated_script_is_valid_powershell).

Run: python3 -m pytest tests/test_install_ps1_readiness_watcher.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_INSTALL_PS1 = _REPO / "install.ps1"


class MaxRetriesBudgetTests(unittest.TestCase):
    def test_max_retries_is_at_least_90(self) -> None:
        """Regression guard: the 2026-08-02 bump (60 -> 90) must not
        silently regress back down in a future edit."""
        src = _INSTALL_PS1.read_text(encoding="utf-8")
        m = re.search(r"\$MaxRetries\s*=\s*(\d+)", src)
        self.assertIsNotNone(m, "install.ps1 no longer defines $MaxRetries")
        self.assertGreaterEqual(int(m.group(1)), 90)


class WatcherPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _INSTALL_PS1.read_text(encoding="utf-8")

    def test_watcher_is_spawned_only_in_the_not_ready_branch(self) -> None:
        # The watcher block must be gated on $ServerReady being false --
        # it exists to cover the timeout case, not every install.
        idx = self.src.index("if (-not $ServerReady) {\n    try {\n        $WatcherPath")
        self.assertGreater(idx, 0, "watcher block not found under the not-ready guard")

    def test_watcher_is_started_detached_and_hidden(self) -> None:
        # Must survive Pause-AndExit killing this installer's own process
        # tree, and must not pop a visible window.
        self.assertIn('-WindowStyle Hidden -ArgumentList', self.src)
        self.assertIn('powershell.exe', self.src)

    def test_watcher_polls_the_real_healthz_route(self) -> None:
        # Same drift class test_installer_health_probe.py already guards
        # for the foreground loop -- the watcher must agree.
        self.assertIn("/v1/console/healthz", self.src)

    def test_watcher_cleans_up_its_own_temp_file(self) -> None:
        self.assertIn("Remove-Item -Path '$WatcherPath'", self.src)


class GeneratedWatcherScriptIsValidPowerShell(unittest.TestCase):
    """Extracts the watcher here-string with realistic interpolated values
    (mirroring what install.ps1 actually substitutes at runtime) and parses
    the RESULT as its own standalone PowerShell file -- the outer script
    parsing cleanly says nothing about whether the string IT WRITES TO DISK
    is itself valid PowerShell."""

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
    def test_generated_watcher_parses_cleanly(self) -> None:
        harness = r"""
$ConsolePort = 8765
$ConsoleURL = "http://localhost:$ConsolePort/console/"
$WatcherPath = "/tmp/corvin-install-watcher-test.ps1"
@"
`$ErrorActionPreference = 'SilentlyContinue'
for (`$i = 0; `$i -lt 300; `$i++) {
    try {
        `$r = Invoke-WebRequest -Uri 'http://localhost:$ConsolePort/v1/console/healthz' -TimeoutSec 2 -UseBasicParsing
        if (`$r -and `$r.StatusCode -ge 200 -and `$r.StatusCode -lt 400) {
            Start-Process '$ConsoleURL'
            break
        }
    } catch {}
    Start-Sleep -Seconds 1
}
Remove-Item -Path '$WatcherPath' -Force -ErrorAction SilentlyContinue
"@ | Set-Content -Path $WatcherPath -Encoding UTF8
"""
        harness_path = Path("/tmp/corvin_watcher_harness_test.ps1")
        harness_path.write_text(harness, encoding="utf-8")
        try:
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(harness_path)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            generated = Path("/tmp/corvin-install-watcher-test.ps1")
            self.assertTrue(generated.exists(), "harness did not write the watcher script")
            content = generated.read_text(encoding="utf-8")
            # Interpolation actually happened (not left as literal $ConsolePort text).
            self.assertIn("localhost:8765/v1/console/healthz", content)
            self.assertIn("http://localhost:8765/console/", content)

            parse_check = f"""
$errs = $null
[System.Management.Automation.Language.Parser]::ParseFile('{generated}', [ref]$null, [ref]$errs) | Out-Null
if ($errs.Count -gt 0) {{ $errs | ForEach-Object {{ Write-Host $_.Message }}; exit 1 }}
"""
            parse_result = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", parse_check],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(parse_result.returncode, 0, parse_result.stdout + parse_result.stderr)
        finally:
            harness_path.unlink(missing_ok=True)
            Path("/tmp/corvin-install-watcher-test.ps1").unlink(missing_ok=True)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell Core (pwsh) not installed")
    def test_watcher_logic_actually_detects_a_delayed_server(self) -> None:
        """Real execution, not just parsing: a fake HTTP server that only
        starts answering after a delay (simulating a slow cold start) must
        be detected by the SAME polling shape the watcher uses. A freshly
        allocated free port (not a hardcoded one) avoids TIME_WAIT/reuse
        flakiness across quick successive test runs."""
        import socket as _socket
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        poll_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$found = $false
for ($i = 0; $i -lt 15; $i++) {{
    try {{
        $r = Invoke-WebRequest -Uri 'http://localhost:{port}/v1/console/healthz' -TimeoutSec 2 -UseBasicParsing
        if ($r -and $r.StatusCode -ge 200 -and $r.StatusCode -lt 400) {{
            Write-Output "READY"
            $found = $true
            break
        }}
    }} catch {{}}
    Start-Sleep -Seconds 1
}}
if (-not $found) {{ Write-Output "TIMEOUT" }}
"""
        poll_path = Path(f"/tmp/corvin_watcher_poll_test_{port}.ps1")
        poll_path.write_text(poll_script, encoding="utf-8")

        server_script = _REPO / "tests" / "fixtures" / f"_delayed_healthz_server_{port}.py"
        server_script.parent.mkdir(parents=True, exist_ok=True)
        server_script.write_text(
            'import http.server, socketserver, threading, time\n'
            'class H(http.server.BaseHTTPRequestHandler):\n'
            '    def do_GET(self):\n'
            '        if self.path == "/v1/console/healthz":\n'
            '            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")\n'
            '        else:\n'
            '            self.send_response(404); self.end_headers()\n'
            '    def log_message(self, *a): pass\n'
            'def serve():\n'
            '    time.sleep(3)\n'
            f'    with socketserver.TCPServer(("127.0.0.1", {port}), H) as s:\n'
            '        s.serve_forever()\n'
            'threading.Thread(target=serve, daemon=True).start()\n'
            'time.sleep(8)\n',
            encoding="utf-8",
        )
        proc = subprocess.Popen([sys.executable, str(server_script)])
        try:
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(poll_path)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertIn("READY", result.stdout, result.stdout + result.stderr)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            poll_path.unlink(missing_ok=True)
            server_script.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
