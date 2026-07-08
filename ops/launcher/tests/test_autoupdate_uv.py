"""Tests for the auto-update command selection (uv-tool vs pip).

The Windows one-line installer uses ``uv tool install``, whose venv has no pip —
so the historical ``python -m pip install corvinos==<latest>`` upgrade silently
failed there and the autostart never updated. ``_pick_upgrade_command`` must pick
``uv tool upgrade`` for uv-managed installs and ``pip install`` otherwise.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_LAUNCHER = _THIS.parents[1]          # ops/launcher
sys.path.insert(0, str(_LAUNCHER))

from corvin import serve_backend as sb  # noqa: E402


class UpgradeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_uv = sb._is_uv_tool_install
        self._orig_pip = sb._pip_available
        self._orig_which = sb.shutil.which

    def tearDown(self) -> None:
        sb._is_uv_tool_install = self._orig_uv
        sb._pip_available = self._orig_pip
        sb.shutil.which = self._orig_which

    def test_pip_install_flavour(self) -> None:
        sb._is_uv_tool_install = lambda: False
        sb._pip_available = lambda: True
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[1:4], ["-m", "pip", "install"])
        self.assertIn("corvinos==0.10.8", cmd)
        self.assertEqual(manual, "pip install corvinos==0.10.8")

    def test_uv_tool_flavour_when_uv_managed(self) -> None:
        sb._is_uv_tool_install = lambda: True
        sb.shutil.which = lambda x: "/home/u/.local/bin/uv" if x == "uv" else None
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertEqual(cmd, ["/home/u/.local/bin/uv", "tool", "upgrade", "corvinos"])
        self.assertEqual(manual, "uv tool upgrade corvinos")

    def test_uv_flavour_when_pip_missing(self) -> None:
        # Not detected as uv-managed by path, but pip is unavailable and uv exists
        # → still prefer uv (a pip command would fail).
        sb._is_uv_tool_install = lambda: False
        sb._pip_available = lambda: False
        sb.shutil.which = lambda x: "/usr/bin/uv" if x == "uv" else None
        cmd, manual = sb._pick_upgrade_command("0.10.8")
        self.assertEqual(cmd, ["/usr/bin/uv", "tool", "upgrade", "corvinos"])

    def test_uv_managed_but_uv_missing_returns_none(self) -> None:
        sb._is_uv_tool_install = lambda: True
        sb._pip_available = lambda: False
        sb.shutil.which = lambda _x: None
        # Also block the ~/.local/bin fallback probe by pointing HOME at a temp.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            orig_home = sb.Path.home
            sb.Path.home = staticmethod(lambda: Path(td))  # type: ignore[assignment]
            try:
                cmd, manual = sb._pick_upgrade_command("0.10.8")
            finally:
                sb.Path.home = orig_home  # type: ignore[assignment]
        self.assertIsNone(cmd)
        self.assertEqual(manual, "uv tool upgrade corvinos")

    def test_detect_uv_tool_install_by_prefix(self) -> None:
        orig_prefix = sb.sys.prefix
        try:
            sb.sys.prefix = "/home/u/.local/share/uv/tools/corvinos"
            self.assertTrue(sb._is_uv_tool_install())
            sb.sys.prefix = "/usr/lib/python3.12"
            self.assertFalse(sb._is_uv_tool_install())
        finally:
            sb.sys.prefix = orig_prefix


class WindowsLiveUpgradeSkipTests(unittest.TestCase):
    """A running process's own interpreter/extension files are locked on
    Windows, so a live in-process self-upgrade reliably fails there (unlike
    POSIX). maybe_pypi_autoupdate() must skip the doomed subprocess attempt
    on Windows and print the manual command instead of trying and failing."""

    def setUp(self) -> None:
        self._orig_platform = sb.sys.platform
        self._orig_run = sb.subprocess.run
        self._orig_pick = sb._pick_upgrade_command

    def tearDown(self) -> None:
        sb.sys.platform = self._orig_platform
        sb.subprocess.run = self._orig_run
        sb._pick_upgrade_command = self._orig_pick

    def test_skips_live_subprocess_on_windows(self) -> None:
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"],
            "uv tool upgrade corvinos",
        )
        called = []
        sb.subprocess.run = lambda *a, **k: called.append((a, k)) or (_ for _ in ()).throw(
            AssertionError("subprocess.run must not be called on Windows")
        )

        import importlib.metadata as _meta
        import json as _json
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return _json.dumps({"info": {"version": "9.9.9"}}).encode()

        orig_version = _meta.version
        orig_urlopen = _ur.urlopen
        _meta.version = lambda _pkg: "0.10.6"
        _ur.urlopen = lambda *a, **k: _FakeResp()
        try:
            sb.maybe_pypi_autoupdate()
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen
        self.assertEqual(called, [])


class PsArrayLiteralTests(unittest.TestCase):
    def test_simple_args(self) -> None:
        self.assertEqual(sb._ps_array_literal(["a", "b"]), '@("a","b")')

    def test_empty_list(self) -> None:
        self.assertEqual(sb._ps_array_literal([]), "@()")

    def test_escapes_quotes_and_backticks(self) -> None:
        out = sb._ps_array_literal(['say "hi"', "back`tick"])
        self.assertEqual(out, '@("say `"hi`"","back``tick")')


class PsQuoteTests(unittest.TestCase):
    """-FilePath binds to [string], not [string[]] -- it must get a plain
    quoted string, never an @(...) array literal (which PowerShell's
    Start-Process parameter binder rejects/coerces unpredictably)."""

    def test_simple_string(self) -> None:
        self.assertEqual(sb._ps_quote("uv"), '"uv"')

    def test_escapes_quotes_and_backticks(self) -> None:
        self.assertEqual(sb._ps_quote('a"b`c'), '"a`"b``c"')

    def test_escapes_dollar_sign_to_prevent_subexpression_injection(self) -> None:
        # Adversarial review finding: inside a PowerShell double-quoted
        # string, $(...) is a live subexpression evaluated at parse time
        # regardless of which cmdlet consumes the resulting string. A CLI
        # arg like --host='$(Start-Process calc)' must NOT survive as a
        # LIVE (non-backtick-escaped) subexpression in the generated script.
        out = sb._ps_quote("$(Start-Process calc)")
        self.assertEqual(out, '"`$(Start-Process calc)"')
        # No raw, un-escaped "$(" (i.e. not preceded by a backtick) remains.
        self.assertNotIn("m$(", out)  # would be the tail of an un-escaped run
        self.assertTrue(out.count("`$(") == 1 and out.count("$(") == 1)

    def test_array_literal_also_escapes_dollar_sign(self) -> None:
        out = sb._ps_array_literal(["--host=$(evil)"])
        self.assertIn("`$(evil)", out)


class WindowsSelfUpdateHandoffTests(unittest.TestCase):
    """With a relaunch_argv provided, Windows must hand off to the detached
    self-updater (and never attempt the doomed live subprocess upgrade)."""

    def setUp(self) -> None:
        self._orig_platform = sb.sys.platform
        self._orig_run = sb.subprocess.run
        self._orig_pick = sb._pick_upgrade_command
        self._orig_spawn = sb._spawn_windows_self_updater
        sb._clear_update_convergence_marker()  # INST-1b: isolate the temp marker

    def tearDown(self) -> None:
        sb.sys.platform = self._orig_platform
        sb.subprocess.run = self._orig_run
        sb._pick_upgrade_command = self._orig_pick
        sb._spawn_windows_self_updater = self._orig_spawn
        sb._clear_update_convergence_marker()

    def _fake_pypi(self, current: str, latest: str):
        import importlib.metadata as _meta
        import json as _json
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return _json.dumps({"info": {"version": latest}}).encode()

        orig_version = _meta.version
        orig_urlopen = _ur.urlopen
        _meta.version = lambda _pkg: current
        _ur.urlopen = lambda *a, **k: _FakeResp()
        return orig_version, orig_urlopen, _meta, _ur

    def test_successful_handoff_returns_true_and_skips_live_upgrade(self) -> None:
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"], "uv tool upgrade corvinos",
        )
        sb.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("subprocess.run must not be called when handing off")
        )
        spawn_calls = []
        sb._spawn_windows_self_updater = lambda cmd, relaunch_argv: (
            spawn_calls.append((cmd, relaunch_argv)) or True
        )

        orig_version, orig_urlopen, _meta, _ur = self._fake_pypi("0.10.6", "9.9.9")
        try:
            result = sb.maybe_pypi_autoupdate(relaunch_argv=["corvin-serve", "--port=8765"])
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen

        self.assertTrue(result)
        self.assertEqual(len(spawn_calls), 1)
        self.assertEqual(spawn_calls[0][1], ["corvin-serve", "--port=8765"])

    def test_failed_handoff_returns_false_with_manual_hint(self) -> None:
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"], "uv tool upgrade corvinos",
        )
        sb._spawn_windows_self_updater = lambda cmd, relaunch_argv: False

        orig_version, orig_urlopen, _meta, _ur = self._fake_pypi("0.10.6", "9.9.9")
        try:
            result = sb.maybe_pypi_autoupdate(relaunch_argv=["corvin-serve"])
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen

        self.assertFalse(result)

    def test_no_relaunch_argv_falls_back_to_manual_message(self) -> None:
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"], "uv tool upgrade corvinos",
        )
        spawn_calls = []
        sb._spawn_windows_self_updater = lambda *a, **k: spawn_calls.append(1) or True

        orig_version, orig_urlopen, _meta, _ur = self._fake_pypi("0.10.6", "9.9.9")
        try:
            result = sb.maybe_pypi_autoupdate()  # no relaunch_argv
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen

        self.assertFalse(result)
        self.assertEqual(spawn_calls, [])


class SupervisedSkipTests(unittest.TestCase):
    """INST-2 / WA-2 / WA-3: when launched by the install.ps1 supervisor
    (CORVIN_SUPERVISED=1), the in-process auto-update must be skipped entirely —
    the supervisor already runs `uv tool upgrade` once per logon, and an
    in-process handoff would fight its 5-per-300s restart budget."""

    def test_supervised_env_skips_autoupdate_before_any_pypi_check(self) -> None:
        import os as _os
        # A network/PyPI touch would prove the skip didn't happen early enough.
        import urllib.request as _ur
        orig_urlopen = _ur.urlopen

        def _boom(*a, **k):
            raise AssertionError("must not touch PyPI when CORVIN_SUPERVISED=1")

        _ur.urlopen = _boom
        _prev = _os.environ.get("CORVIN_SUPERVISED")
        _os.environ["CORVIN_SUPERVISED"] = "1"
        try:
            self.assertFalse(sb.maybe_pypi_autoupdate(relaunch_argv=["corvin-serve"]))
        finally:
            _ur.urlopen = orig_urlopen
            if _prev is None:
                _os.environ.pop("CORVIN_SUPERVISED", None)
            else:
                _os.environ["CORVIN_SUPERVISED"] = _prev


class ConvergenceGuardTests(unittest.TestCase):
    """INST-1b: the Windows self-update handoff must not loop forever when an
    upgrade fails to advance the installed version — a second handoff for the
    SAME target version is refused."""

    def setUp(self) -> None:
        sb._clear_update_convergence_marker()

    def tearDown(self) -> None:
        sb._clear_update_convergence_marker()

    def test_first_target_allowed_second_same_target_refused(self) -> None:
        # M3: _update_convergence_ok is now read-only; the marker is armed by
        # _record_update_attempt AFTER a successful spawn.
        self.assertTrue(sb._update_convergence_ok("1.2.3"))   # first handoff ok
        sb._record_update_attempt("1.2.3")                    # armed post-spawn
        self.assertFalse(sb._update_convergence_ok("1.2.3"))  # loop → refused

    def test_new_target_after_refusal_is_allowed(self) -> None:
        self.assertTrue(sb._update_convergence_ok("1.2.3"))
        sb._record_update_attempt("1.2.3")
        self.assertFalse(sb._update_convergence_ok("1.2.3"))
        # A genuinely newer release must not be blocked by the stale marker.
        self.assertTrue(sb._update_convergence_ok("1.2.4"))

    def test_failed_spawn_leaves_no_marker_so_retry_allowed(self) -> None:
        # M3: a spawn that never started must NOT arm the guard — no
        # _record_update_attempt call means the same target is retried.
        self.assertTrue(sb._update_convergence_ok("1.2.3"))
        # (no _record_update_attempt — simulates a failed handoff)
        self.assertTrue(sb._update_convergence_ok("1.2.3"))

    def test_marker_ttl_expiry_allows_retry(self) -> None:
        # M3: a non-converging attempt self-heals once the marker ages past the
        # TTL — auto-update no longer freezes until a newer release ships.
        import os as _os
        sb._record_update_attempt("1.2.3")
        self.assertFalse(sb._update_convergence_ok("1.2.3"))  # within TTL → refused
        marker = sb._update_marker_path()
        stale = time.time() - (sb._UPDATE_MARKER_TTL_SECONDS + 60)
        _os.utime(marker, (stale, stale))
        self.assertTrue(sb._update_convergence_ok("1.2.3"))   # aged out → retry

    def test_marker_cleared_on_converged_up_to_date(self) -> None:
        sb._record_update_attempt("1.2.3")
        sb._clear_update_convergence_marker()
        # After clearing (what the "up to date" branch does), the same target
        # is allowed again.
        self.assertTrue(sb._update_convergence_ok("1.2.3"))

    def test_second_handoff_for_same_target_refused_end_to_end(self) -> None:
        orig_platform = sb.sys.platform
        orig_pick = sb._pick_upgrade_command
        orig_spawn = sb._spawn_windows_self_updater
        sb.sys.platform = "win32"
        sb._pick_upgrade_command = lambda latest: (
            ["uv", "tool", "upgrade", "corvinos"], "uv tool upgrade corvinos",
        )
        spawn_calls = []
        sb._spawn_windows_self_updater = lambda cmd, argv: spawn_calls.append(1) or True

        import importlib.metadata as _meta
        import json as _json
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return _json.dumps({"info": {"version": "9.9.9"}}).encode()

        orig_version = _meta.version
        orig_urlopen = _ur.urlopen
        _meta.version = lambda _pkg: "0.10.6"  # never advances (simulated no-op)
        _ur.urlopen = lambda *a, **k: _FakeResp()
        try:
            first = sb.maybe_pypi_autoupdate(relaunch_argv=["corvin-serve"])
            second = sb.maybe_pypi_autoupdate(relaunch_argv=["corvin-serve"])
        finally:
            _meta.version = orig_version
            _ur.urlopen = orig_urlopen
            sb.sys.platform = orig_platform
            sb._pick_upgrade_command = orig_pick
            sb._spawn_windows_self_updater = orig_spawn

        self.assertTrue(first)             # first handoff proceeds
        self.assertFalse(second)           # second (same target) refuses → no loop
        self.assertEqual(len(spawn_calls), 1)


class SpawnWindowsSelfUpdaterScriptGenTests(unittest.TestCase):
    """Exercises the real script-generation path (not mocked out), reading
    back the actual .ps1 written to disk -- catches injection/quoting bugs
    that a fully-mocked test would hide."""

    def setUp(self) -> None:
        self._orig_popen = sb.subprocess.Popen
        self._orig_which = sb.shutil.which
        self._written_scripts: list[str] = []

    def tearDown(self) -> None:
        sb.subprocess.Popen = self._orig_popen
        sb.shutil.which = self._orig_which
        import glob
        import os as _os
        for p in glob.glob("/tmp/corvin-self-update-*.ps1") if sys.platform != "win32" else []:
            try:
                _os.remove(p)
            except OSError:
                pass

    def test_malicious_host_arg_cannot_inject_subexpression(self) -> None:
        """The exact PoC from the adversarial review: a --host value
        containing $(...) must never appear un-escaped in the generated
        script, in EITHER the Start-Process call or the Log lines."""
        sb.subprocess.Popen = lambda *a, **k: None
        sb.shutil.which = lambda _name: None  # force fallback to bare name

        evil = "$(Start-Process calc)"
        sb._spawn_windows_self_updater(
            ["uv", "tool", "upgrade", "corvinos"],
            ["corvin-serve", f"--host={evil}"],
        )

        import glob
        matches = glob.glob(f"{__import__('tempfile').gettempdir()}/corvin-self-update-*.ps1")
        self.assertTrue(matches, "script was not written")
        content = matches[-1]
        with open(content, encoding="utf-8") as fh:
            script = fh.read()
        # The un-escaped injection (no backtick before the $) must not
        # appear anywhere -- that would be a live PowerShell subexpression.
        self.assertNotIn("--host=$(Start-Process calc)", script)
        # The escaped form (backtick before $) must be present instead.
        self.assertIn("--host=`$(Start-Process calc)", script)

    def test_upgrade_start_process_avoids_console_less_no_new_window(self) -> None:
        """Regression: the detached PowerShell host has no console (it is
        launched with DETACHED_PROCESS), so `Start-Process -NoNewWindow`
        throws ("This command cannot be run due to the error: The parameter
        is incorrect") because there is no parent console to attach to. That
        exception is terminating and was previously uncaught, silently
        killing the updater right after logging "running upgrade: ..." --
        no relaunch, no error surfaced, matching the observed symptom of
        corvin-serve just returning to an empty prompt after "restarting
        shortly ...". The fix uses -WindowStyle Hidden (like the relaunch
        call already did) and wraps both Start-Process calls in try/catch
        so any future failure is logged instead of dying silently."""
        sb.subprocess.Popen = lambda *a, **k: None
        sb.shutil.which = lambda _name: None

        sb._spawn_windows_self_updater(
            ["uv", "tool", "upgrade", "corvinos"],
            ["corvin-serve"],
        )

        import glob
        matches = glob.glob(f"{__import__('tempfile').gettempdir()}/corvin-self-update-*.ps1")
        with open(matches[-1], encoding="utf-8") as fh:
            script = fh.read()
        self.assertNotIn("-NoNewWindow", script)
        self.assertIn("-WindowStyle Hidden -Wait -PassThru", script)
        self.assertEqual(script.count("try {"), 2)
        self.assertEqual(script.count("} catch {"), 2)

    def test_relaunch_exe_resolved_to_absolute_path_via_which(self) -> None:
        """relaunch_argv[0] must be resolved through shutil.which() in THIS
        process's environment before being embedded, not left as a bare
        command name relying on the detached script's inherited PATH."""
        sb.subprocess.Popen = lambda *a, **k: None
        resolved = "C:\\Users\\test\\.local\\bin\\corvin-serve.exe"
        sb.shutil.which = lambda name: resolved if name == "corvin-serve" else None

        sb._spawn_windows_self_updater(
            ["uv", "tool", "upgrade", "corvinos"],
            ["corvin-serve", "--port=8765"],
        )

        import glob
        matches = glob.glob(f"{__import__('tempfile').gettempdir()}/corvin-self-update-*.ps1")
        with open(matches[-1], encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn(resolved, script)
        self.assertNotIn('-FilePath "corvin-serve"', script)


if __name__ == "__main__":
    unittest.main()
