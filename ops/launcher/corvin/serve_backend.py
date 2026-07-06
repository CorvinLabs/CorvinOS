"""Native serve backend — runs the CorvinOS console via uvicorn directly.

Used by ``corvin serve`` and as a fallback in ``corvin start`` when Docker
is not available.  No container runtime needed; only Python + the
``corvinOS[console]`` extras (FastAPI, uvicorn, ...).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

_DEFAULT_PORT = 8765
_CONSOLE_MODULE = "corvin_console.standalone"
_APP_FACTORY = f"{_CONSOLE_MODULE}:create_app"


# ── Availability check ────────────────────────────────────────────────────────


def unavailable_reason() -> tuple[str | None, str]:
    """Classify why the console cannot start.

    Returns a ``(reason, detail)`` tuple:

    * ``(None, "")``      — available, the console can start.
    * ``("imports", "")`` — the Python backend (``corvin_console`` or
      ``uvicorn``) is not importable; the fix is to (re)install the package.
    * ``("spa", <dir>)``  — the backend is importable but the pre-built SPA
      ``dist/`` is missing; the fix is to run the web-next build step. The
      ``detail`` is the ``web-next`` source dir to ``cd`` into.

    The two cases are kept distinct because they require completely different
    remediation: a pip (re)install versus an npm build.
    """
    # 1. Backend imports (corvin_console + uvicorn).
    for mod in ("corvin_console", "uvicorn"):
        try:
            if importlib.util.find_spec(mod) is None:  # type: ignore[attr-defined]
                return "imports", ""
        except (ModuleNotFoundError, ValueError):
            return "imports", ""

    # 2. Pre-built SPA dist.
    try:
        pkg_dir = Path(importlib.util.find_spec("corvin_console").origin).parent  # type: ignore[union-attr]
    except Exception:
        return "imports", ""

    web_next = pkg_dir / "web-next"
    dist = web_next / "dist"
    if not dist.exists():
        return "spa", str(web_next)

    return None, ""


def is_available() -> bool:
    """Return True when the console extras are installed and the SPA is built."""
    return unavailable_reason()[0] is None


def console_url(port: int = _DEFAULT_PORT) -> str:
    return f"http://localhost:{port}"


# ── Auto-update ───────────────────────────────────────────────────────────────


def _is_uv_tool_install() -> bool:
    """True when corvinos runs from a ``uv tool install`` managed venv.

    uv installs each tool into ``…/uv/tools/<name>/`` and — crucially — that
    venv has **no pip**, so ``python -m pip install`` (the historical upgrade
    path) fails there. The Windows one-line installer uses ``uv tool install``,
    so on Windows this is the common case and the reason autostart upgrades were
    silently no-op'ing.
    """
    probe = str(Path(sys.prefix)).replace("\\", "/").lower()
    return "/uv/tools/" in probe or probe.rstrip("/").endswith("/tools/corvinos")


def _pip_available() -> bool:
    return importlib.util.find_spec("pip") is not None


def _pick_upgrade_command(latest: str) -> tuple[list[str] | None, str]:
    """Choose the right upgrade command for this install flavour.

    Returns ``(argv, manual_hint)``. ``argv`` is None when we know the flavour
    but cannot find the tool to run it (so the caller prints the manual hint
    instead of running a broken command).
    """
    uv = shutil.which("uv")
    # Windows %USERPROFILE%\.local\bin is often not on the Task-Scheduler PATH.
    if not uv:
        for cand in (Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
                     Path.home() / ".cargo" / "bin" / ("uv.exe" if os.name == "nt" else "uv")):
            if cand.is_file():
                uv = str(cand)
                break

    if _is_uv_tool_install() or (uv and not _pip_available()):
        if uv:
            # `uv tool upgrade` pulls the latest compatible release (we already
            # confirmed a newer one exists), and reuses the tool's own venv.
            return [uv, "tool", "upgrade", "corvinos"], "uv tool upgrade corvinos"
        return None, "uv tool upgrade corvinos"  # uv-managed but uv not found

    return (
        [sys.executable, "-m", "pip", "install", f"corvinos=={latest}", "--quiet"],
        f"pip install corvinos=={latest}",
    )


def _ps_quote(s: str) -> str:
    """Quote a single string for embedding in PowerShell source, e.g. -FilePath
    (which binds to [string], NOT [string[]] — an array literal there either
    fails to bind or coerces unpredictably depending on PowerShell version).

    Backtick MUST be escaped first (it's the escape char itself), then `"`.
    `$` is ALSO escaped: inside a PowerShell double-quoted string, `$(...)` /
    `$env:...` / `$variable` are live subexpressions that PowerShell evaluates
    at parse time regardless of which cmdlet consumes the resulting string —
    without this, a CLI arg (e.g. --host) containing `$(...)` is arbitrary
    PowerShell code execution in the generated self-update script."""
    return '"' + s.replace("`", "``").replace('"', '`"').replace("$", "`$") + '"'


def _ps_array_literal(items: list[str]) -> str:
    """Render a PowerShell array literal, e.g. @("a","b") — used for
    -ArgumentList so each arg survives as its own token (no shell re-splitting).
    """
    return "@(" + ",".join(_ps_quote(i) for i in items) + ")"


def _spawn_windows_self_updater(cmd: list[str], relaunch_argv: list[str]) -> bool:
    """Hand off the upgrade to a detached PowerShell script and return True.

    We cannot upgrade our own running venv in place (Windows locks this
    process's own interpreter/extension files for its lifetime), but a
    SEPARATE, short-lived process can: wait for this PID to fully exit, run
    the upgrade, then relaunch corvin-serve — so the update actually applies
    automatically instead of requiring the user to run a command by hand.

    The script is detached (CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS) so it
    keeps running after this process exits, and it logs every step to a file
    in %TEMP% since nothing will be attached to a console by the time most of
    it runs. Caller must exit promptly after this returns True so the target
    files actually become unlocked.
    """
    import tempfile
    import textwrap

    try:
        pid = os.getpid()

        # Resolve the relaunch executable to an absolute path NOW, in this
        # process's own environment/PATH — the detached PowerShell script may
        # inherit a different (e.g. Task-Scheduler-stripped) PATH by the time
        # it actually runs, and a bare "corvin-serve" would then fail to
        # resolve, silently leaving the server down after a successful
        # upgrade. Falls back to the bare name if resolution fails (matches
        # the previous behaviour rather than aborting the handoff).
        relaunch_exe = shutil.which(relaunch_argv[0]) or relaunch_argv[0]
        relaunch_argv = [relaunch_exe, *relaunch_argv[1:]]

        # Every piece of dynamic text — including inside Log "..." calls —
        # MUST go through _ps_quote(). Splicing raw text into the script
        # source is a parse-time (a stray `"`) or execution-time (a `$(...)`)
        # injection risk, and either one can corrupt or hijack this script.
        log_path = Path(tempfile.gettempdir()) / "corvin-self-update.log"
        script_path = Path(tempfile.gettempdir()) / f"corvin-self-update-{pid}.ps1"
        cmd_str = " ".join(cmd)
        relaunch_str = " ".join(relaunch_argv)
        script = textwrap.dedent(f"""
            $ErrorActionPreference = "Continue"
            $log = {_ps_quote(str(log_path))}
            function Log($m) {{ Add-Content -Path $log -Value "$(Get-Date -Format o) $m" }}
            Log {_ps_quote(f"waiting for corvin-serve (pid {pid}) to exit")}
            while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{
                Start-Sleep -Milliseconds 400
            }}
            Log {_ps_quote(f"pid {pid} exited -- running upgrade: {cmd_str}")}
            $p = Start-Process -FilePath {_ps_quote(cmd[0])} `
                -ArgumentList {_ps_array_literal(cmd[1:])} `
                -NoNewWindow -Wait -PassThru
            if ($p.ExitCode -ne 0) {{
                Log {_ps_quote(f"upgrade FAILED (exit code below) -- corvin-serve NOT relaunched. Run manually: {cmd_str}")}
                Log "exit code: $($p.ExitCode)"
                exit 1
            }}
            Log {_ps_quote(f"upgrade ok -- relaunching: {relaunch_str}")}
            Start-Process -FilePath {_ps_quote(relaunch_argv[0])} `
                -ArgumentList {_ps_array_literal(relaunch_argv[1:])} `
                -WindowStyle Hidden
            Log "relaunch dispatched"
        """).strip()
        script_path.write_text(script, encoding="utf-8")

        powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            creationflags=creationflags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"(log: {log_path})")
        return True
    except Exception as exc:  # noqa: BLE001 — never block startup over this
        print(f"(self-update handoff failed: {exc} — continuing without update)")
        return False


def maybe_pypi_autoupdate(relaunch_argv: list[str] | None = None) -> bool:
    """Upgrade corvinos to the latest PyPI release if auto_update is enabled.

    Best-effort — never blocks or fails startup. Reads
    ~/.config/corvin-launcher/config.json for the auto_update flag. Uses
    ``uv tool upgrade`` for uv-managed installs (the Windows default) and
    ``pip install`` for pip installs.

    Returns True when the caller must exit IMMEDIATELY (without starting the
    server) because a Windows self-update handoff was just spawned — the
    detached updater needs this process's files to become unlocked. Returns
    False in every other case (nothing to do, or a live upgrade already ran).
    """
    import json as _json  # noqa: PLC0415
    config_path = Path.home() / ".config" / "corvin-launcher" / "config.json"
    enabled = True
    try:
        data = _json.loads(config_path.read_text(encoding="utf-8"))
        if "auto_update" in data:
            enabled = bool(data["auto_update"])
    except Exception:
        pass

    if not enabled:
        return False

    print("  Checking for updates …", end=" ", flush=True)
    try:
        # Step 1: check PyPI for the latest version (no install yet).
        import importlib.metadata as _meta  # noqa: PLC0415
        import urllib.request as _ur         # noqa: PLC0415
        current = _meta.version("corvinos")
        with _ur.urlopen(  # noqa: S310
            "https://pypi.org/pypi/corvinos/json", timeout=10
        ) as _r:
            latest = __import__("json").loads(_r.read())["info"]["version"]
        if latest == current:
            print(f"up to date ({current})")
            return False
        # Step 2: a newer version exists — attempt upgrade with the command that
        # matches this install flavour (uv tool vs pip).
        print(f"upgrading {current} → {latest} …", end=" ", flush=True)
        cmd, manual = _pick_upgrade_command(latest)
        if cmd is None:
            print(f"\n  ⚠ auto-upgrade needs uv. Run manually:\n    {manual}")
            return False

        if sys.platform.startswith("win"):
            # A live self-upgrade would try to overwrite this exact process's own
            # interpreter/extension files (python.exe, compiled .pyd deps) from
            # inside the still-running process — Windows keeps those files locked
            # for the process's lifetime (unlike POSIX, where an open file's inode
            # can be replaced while it's running), so an in-place attempt would
            # reliably fail with an "Access is denied" / "used by another process"
            # error. Instead, hand off to a detached helper that waits for THIS
            # process to exit, runs the upgrade, then relaunches corvin-serve —
            # so the update actually applies without manual intervention. Falls
            # back to the manual-command hint only if the handoff itself fails,
            # or if the caller didn't provide a relaunch command.
            if relaunch_argv is None:
                print(
                    f"\n  ⚠ a newer version ({latest}) is available, but auto-update "
                    "while running isn't supported on Windows (this process's own "
                    "files are locked). Stop this server (Ctrl-C) and run:\n"
                    f"    {manual}"
                )
                return False
            print("handing off to background updater …", end=" ", flush=True)
            if _spawn_windows_self_updater(cmd, relaunch_argv):
                print(f"\n  ⏳ upgrading to {latest} in the background — restarting shortly …")
                return True
            print(
                f"\n  ⚠ background handoff failed. Stop this server (Ctrl-C) and run:\n"
                f"    {manual}"
            )
            return False

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
            text=True,
        )
        if result.returncode == 0:
            print("done — restart corvin-serve to apply")
        else:
            # upgrade failed (UAC, network, read-only env, …) — show the actual
            # error so failures are diagnosable instead of a bare "failed", and
            # tell the user the exact command to run instead of silently continuing.
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            detail_line = detail[-1] if detail else "no output captured"
            print(
                f"\n  ⚠ auto-upgrade failed ({detail_line}). Run manually:\n    {manual}"
            )
        return False
    except subprocess.TimeoutExpired:
        print("(timed out — continuing)")
        return False
    except Exception:
        print("(update check skipped)")
        return False


# ── Telemetry notice (one-time, opt-out) ──────────────────────────────────────

_TELEMETRY_NOTICE_FILE = Path.home() / ".corvin" / "aco" / "telemetry" / ".notice_shown"


def _show_telemetry_notice_once() -> None:
    """Print a one-time disclosure about the anonymous activity ping.

    The ping is opt-out (default ON): it sends only a pseudonymous instance
    token + the installed version to count how many instances are active.
    No personal data is transmitted. This message is shown exactly once per
    installation; it will not appear again after the user has seen it.
    """
    try:
        if _TELEMETRY_NOTICE_FILE.exists():
            return
        print(
            "\n  CorvinOS sends a daily anonymous ping (version + pseudonymous ID)\n"
            "  to count active instances. No personal data is included.\n"
            "  To opt out: corvin config set telemetry.ping_enabled false\n"
        )
        _TELEMETRY_NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TELEMETRY_NOTICE_FILE.touch()
    except Exception:  # noqa: BLE001
        pass


# ── Startup ping ──────────────────────────────────────────────────────────────


def _fire_startup_ping() -> None:
    """Start the recurring opt-out activity ping check in a daemon thread.

    corvin-serve uses corvin_console.standalone, which has no FastAPI lifespan
    and therefore never starts the boot-healer background task that normally
    re-invokes ping_if_due() every 5 minutes for the gateway/systemd path.
    Previously this called ping_if_due() exactly ONCE, so a long-running
    corvin-serve process (the primary pip/uv install path) sent the daily
    ping on day 1 and then never again — silently dropping out of
    active_7d/active_30d for the rest of its uptime despite staying up and
    in active use (adversarial review finding). start_ping_thread() re-checks
    hourly instead (ping_if_due itself still self-throttles to once/24h).
    Fail-soft: any exception is silently swallowed — startup must never block.
    """
    def _ping() -> None:
        try:
            import time as _t                                    # noqa: PLC0415
            _t.sleep(6)          # wait for uvicorn to finish binding
            from corvin_console.aco.htrace_uploader import start_ping_thread  # noqa: PLC0415
            from forge import paths as _p                        # noqa: PLC0415
            start_ping_thread(_p.corvin_home())
        except Exception:                                        # noqa: BLE001
            pass

    threading.Thread(target=_ping, daemon=True).start()


def _start_heartbeat() -> None:
    """Start the 5-minute presence heartbeat in a daemon thread."""
    def _hb() -> None:
        try:
            from forge import paths as _p  # type: ignore[import]
            from corvin_console.aco.heartbeat import start_heartbeat_thread
            start_heartbeat_thread(_p.corvin_home())
        except Exception:                                        # noqa: BLE001
            pass
    threading.Thread(target=_hb, daemon=True).start()


# ── Start ─────────────────────────────────────────────────────────────────────


def start(
    port: int = _DEFAULT_PORT,
    *,
    open_browser: bool = True,
    open_path: str = "/console/",
    host: str = "127.0.0.1",
    log_level: str = "warning",
) -> int:
    """Start the console with uvicorn and (optionally) open the browser.

    open_path: path appended to the console URL for the browser open. Defaults to
    the console SPA root ``/console/`` — the actual web UI — NOT the raw
    ``/v1/console/auth/local-login`` API endpoint. The SPA orchestrates the
    localhost auto-login itself (RequireAuth → LoginPage → local-login → session
    → /console/app), so the user always lands in the real console UI and never on
    a raw JSON page if anything (rate-limit, error) goes wrong on the auth call.
    This matches what ``bridge.sh console`` opens. (Opening the API endpoint
    directly was the previous default and surfaced "too many login attempts" JSON
    in the browser when the auto-login was rate-limited.)

    Blocks until the server is stopped (Ctrl-C).
    Returns the uvicorn process exit code.
    """
    url = console_url(port)
    _show_telemetry_notice_once()
    _fire_startup_ping()
    _start_heartbeat()

    if open_browser:
        _schedule_browser_open(url.rstrip("/") + open_path, delay=1.6)

    env = os.environ.copy()
    # local-login is on by default; only disable if caller explicitly set it to 0
    env.setdefault("CORVIN_LOCAL_AUTOLOGIN", "1")
    # Pin CORVIN_HOME so every component in the console process agrees on the
    # same root — mirrors bridge.sh console's explicit pinning (without it,
    # components that are imported from different sys.path contexts can disagree
    # when the repo's paths.py and a vendored copy both walk their own __file__).
    if "CORVIN_HOME" not in env:
        try:
            import importlib.util as _ilu  # noqa: PLC0415
            spec = _ilu.find_spec("forge.paths")
            if spec and spec.origin:
                _paths_mod_dir = Path(spec.origin).parent
                # walk up from forge/paths.py looking for .corvin_repo
                _ch = None
                for _p in [_paths_mod_dir, *_paths_mod_dir.parents]:
                    if (_p / ".corvin_repo").exists() or (_p / "plugins").is_dir():
                        _ch = str(_p / ".corvin")
                        break
                if _ch:
                    env["CORVIN_HOME"] = _ch
        except Exception:  # noqa: BLE001 — best-effort; falls back to paths.py auto-detect
            pass

    cmd = [
        sys.executable, "-m", "uvicorn",
        _APP_FACTORY,
        "--factory",
        "--host", host,
        "--port", str(port),
        "--log-level", log_level,
    ]
    # Windows: pin the stdlib asyncio loop. The default policy on Python 3.8+ is
    # the ProactorEventLoop, which is REQUIRED for asyncio.create_subprocess_exec
    # (how every engine/OS-turn is spawned) — a SelectorEventLoop raises
    # NotImplementedError on subprocess spawn. `--loop asyncio` keeps the default
    # (Proactor) policy and avoids any uvloop selector fallback. On POSIX we leave
    # the default `auto` so uvloop is still used (no perf regression).
    if sys.platform == "win32":
        cmd += ["--loop", "asyncio"]

    try:
        result = subprocess.run(cmd, env=env)
        return result.returncode
    except KeyboardInterrupt:
        return 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _schedule_browser_open(url: str, delay: float) -> None:
    """Open *url* in the default browser after *delay* seconds (daemon thread)."""
    def _open() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    t = threading.Thread(target=_open, daemon=True)
    t.start()
