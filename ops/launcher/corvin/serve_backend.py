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
import socket
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


def _console_already_running(host: str, port: int) -> bool:
    """True if something is already accepting TCP connections on host:port.

    A cheap connect probe, not an HTTP healthz call: the goal here is only
    "would launching uvicorn collide with an existing listener", which a
    bare connect answers just as reliably and without needing the console's
    own routes to be up yet.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


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


def _find_uv() -> str | None:
    """Locate the uv binary, probing the usual install dirs when it is not on
    PATH — Windows %USERPROFILE%\\.local\\bin is often not on the
    Task-Scheduler PATH."""
    uv = shutil.which("uv")
    if not uv:
        for cand in (Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
                     Path.home() / ".cargo" / "bin" / ("uv.exe" if os.name == "nt" else "uv")):
            if cand.is_file():
                uv = str(cand)
                break
    return uv


def _pick_upgrade_command(latest: str) -> tuple[list[str] | None, str]:
    """Choose the right upgrade command for this install flavour.

    Returns ``(argv, manual_hint)``. ``argv`` is None when we know the flavour
    but cannot find the tool to run it (so the caller prints the manual hint
    instead of running a broken command).
    """
    uv = _find_uv()

    if _is_uv_tool_install() or (uv and not _pip_available()):
        if uv:
            # `uv tool upgrade` pulls the latest compatible release (we already
            # confirmed a newer one exists via a direct, uncached PyPI JSON
            # query), and reuses the tool's own venv.
            #
            # --reinstall-package corvinos (2026-07-29, found via adversarial
            # review): WITHOUT this, `uv tool upgrade` resolves against uv's
            # OWN cached view of the package index — a SEPARATE cache from
            # the raw PyPI JSON check above, which can lag behind it (fresh
            # upload not yet visible to uv's resolver). When that happens,
            # `uv tool upgrade` finds "nothing newer than what's installed",
            # does nothing, and still exits 0 — indistinguishable from a real
            # upgrade by exit code alone. `--reinstall-package` implies
            # `--refresh-package`, forcing a fresh index lookup for exactly
            # this package (not a full `--no-cache`, which would needlessly
            # slow down resolving every other dependency too). Observed live:
            # a real 0.10.70 -> 0.10.71 handoff exited 0 but never converged.
            return (
                [uv, "tool", "upgrade", "corvinos", "--reinstall-package", "corvinos"],
                "uv tool upgrade corvinos --reinstall-package corvinos",
            )
        return None, "uv tool upgrade corvinos --reinstall-package corvinos"  # uv-managed but uv not found

    return (
        [sys.executable, "-m", "pip", "install", f"corvinos=={latest}", "--quiet"],
        f"pip install corvinos=={latest}",
    )


# ── Post-upgrade browser provisioning (I2, 2026-07-20) ────────────────────────
# A bare `uv tool upgrade corvinos` rebuilds the tool venv strictly from the uv
# receipt: (a) playwright pip-injected by early installers is wiped, and
# (b) 0.10.45–0.10.47-era installs (whose installer never fetched Chromium)
# would NEVER get the browser. After a successful upgrade we therefore
# best-effort re-ensure the [browser] extra + the Chromium binary. Everything
# here is fail-soft and runs in a daemon thread — a failure or slow download
# must never block or delay server startup.

_BROWSER_PROVISION_TIMEOUT = 900  # seconds — bounded, the download is ~150 MB

# Probe run in the (possibly freshly rebuilt) tool venv — mirrors the
# installer's _chromium_present() without importing playwright in-process
# (this process may predate the rebuild).
_CHROMIUM_PROBE_SRC = (
    "import os, sys\n"
    "from playwright.sync_api import sync_playwright\n"
    "with sync_playwright() as pw:\n"
    "    p = pw.chromium.executable_path\n"
    "sys.exit(0 if p and os.path.exists(p) else 1)\n"
)


def _browser_extra_present() -> bool:
    """True when the venv behind sys.executable can import playwright."""
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import playwright"],
            capture_output=True, timeout=60,
        )
        return probe.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _playwright_chromium_present() -> bool:
    """True when Playwright (in the venv behind sys.executable) can resolve an
    existing Chromium executable."""
    try:
        probe = subprocess.run(
            [sys.executable, "-c", _CHROMIUM_PROBE_SRC],
            capture_output=True, timeout=120,
        )
        return probe.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _ensure_browser_provisioned() -> None:
    """Best-effort browser re-provisioning after an auto-update. Fail-soft:
    logs and swallows every failure — the server keeps running regardless."""
    try:
        if not _browser_extra_present():
            if not _is_uv_tool_install():
                # pip flavour: don't second-guess the environment — point at
                # the entry point that provisions with the right interpreter.
                print("  ℹ browser automation is missing — finish with: corvin-install --browser")
                return
            uv = _find_uv()
            if uv is None:
                print("  ℹ browser extra missing and uv not found — run: "
                      "uv tool install --force 'corvinos[browser]'")
                return
            # Restore the extra INTO THE RECEIPT (not pip-inject) so the next
            # `uv tool upgrade` keeps it. POSIX-safe while running: the venv
            # files are replaced by inode, our open handles stay valid.
            print("  restoring the corvinos[browser] extra (lost in upgrade) …")
            r = subprocess.run(
                [uv, "tool", "install", "--force", "corvinos[browser]"],
                capture_output=True, text=True, timeout=_BROWSER_PROVISION_TIMEOUT,
            )
            if r.returncode != 0 or not _browser_extra_present():
                print("  ⚠ could not restore the [browser] extra — run: "
                      "uv tool install --force 'corvinos[browser]'")
                return
        if _playwright_chromium_present():
            return
        print("  downloading Playwright Chromium (~150 MB, one-time) …")
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, timeout=_BROWSER_PROVISION_TIMEOUT,
        )
        if r.returncode == 0 and _playwright_chromium_present():
            print("  ✓ browser automation provisioned")
        else:
            print("  ⚠ Chromium download failed — finish with: corvin-install --browser")
    except Exception as exc:  # noqa: BLE001 — never propagate into startup
        try:
            print(f"(browser provisioning skipped: {exc})")
        except Exception:  # noqa: BLE001
            pass


def _provision_browser_after_upgrade() -> None:
    """Run the provisioning check in a daemon thread — never blocks startup."""
    try:
        threading.Thread(
            target=_ensure_browser_provisioned,
            name="corvin-browser-provision",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        pass


def _update_marker_path() -> Path:
    """Temp marker recording the version the Windows self-updater last handed
    off for — used by the convergence guard (INST-1b)."""
    import tempfile  # noqa: PLC0415
    return Path(tempfile.gettempdir()) / "corvin-self-update-target.txt"


def _clear_update_convergence_marker() -> None:
    try:
        _update_marker_path().unlink()
    except Exception:  # noqa: BLE001
        pass


# M3: bounded no-retry window. A non-converging handoff (marker == target)
# refuses further attempts, but only for this long — after the TTL a transient
# failure (PyPI/network hiccup, uv upgrade that never completed) self-heals and
# the same target may be retried, instead of freezing auto-update until a NEWER
# release happens to ship.
_UPDATE_MARKER_TTL_SECONDS = 6 * 3600  # 6 hours


def _update_convergence_ok(target: str) -> bool:
    """Convergence guard for the Windows self-update handoff (INST-1b).

    Checks the temp marker recording the version a previous handoff was made
    for. If the marker already names this exact target AND is still within the
    TTL window, a previous handoff relaunched us but the installed version did
    NOT advance to it (a non-converging upgrade — e.g. a pinned uv receipt
    freezing ``uv tool upgrade`` to a no-op, or a PyPI/uv hiccup). Refuse a
    second handoff for the same target so the relaunch cycle can't spin forever;
    the server just keeps running the current version.

    The marker is mtime-TTL'd (``_UPDATE_MARKER_TTL_SECONDS``): once it ages out,
    a transient failure self-heals and the same target may be retried.

    This function only READS state — the marker is written by
    ``_record_update_attempt`` AFTER a successful handoff, so a spawn that never
    got off the ground doesn't arm the guard.

    Returns True when a handoff for *target* is allowed, False when it must be
    refused (already attempted within the TTL, didn't converge).
    """
    marker = _update_marker_path()
    try:
        already = marker.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        already = ""
    if already != target:
        return True
    # Same target already handed off for — honour the refusal only within the
    # TTL window so a transient, non-converging attempt eventually retries.
    try:
        age = time.time() - marker.stat().st_mtime
    except Exception:  # noqa: BLE001
        age = 0.0
    if age > _UPDATE_MARKER_TTL_SECONDS:
        return True
    return False


def _record_update_attempt(target: str) -> None:
    """Persist that a handoff for *target* was made. Call this only AFTER
    ``_spawn_windows_self_updater`` returns success, so the convergence guard is
    armed only once a relaunch is actually inbound (a failed spawn leaves no
    marker and is freely retried on the next start)."""
    try:
        _update_marker_path().write_text(target, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


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


def _spawn_windows_self_updater(
    cmd: list[str], relaunch_argv: list[str], *, target_version: str = "",
) -> bool:
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

    AVAILABILITY INVARIANT (2026-07-29 — found via adversarial review after a
    live non-convergence): the ORIGINAL corvin-serve process has ALREADY
    exited by the time this script's upgrade step runs (that is the whole
    reason a detached script is needed at all). A previous version of this
    script `exit 1`'d — WITHOUT relaunching — on either an upgrade launch
    exception or a non-zero exit code. That is a guaranteed TOTAL OUTAGE on
    any transient upgrade failure (network blip, permission error, antivirus
    quarantine, disk full, temporary PyPI hiccup) — the one thing this whole
    mechanism must never cause. The script now ALWAYS relaunches at the end,
    regardless of whether the upgrade step succeeded: a failed upgrade just
    means the relaunch picks up the version already on disk (fail-open to
    "still running the old, working version"), never "nothing running".
    Every failure is still logged clearly, so it is diagnosable — it is just
    no longer fatal to the relaunch.
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
        # Version-convergence check (2026-07-29): `uv tool upgrade`'s exit
        # code alone cannot distinguish "upgraded successfully" from
        # "resolved against a stale cached index, found nothing to do, exited
        # 0 anyway" — the second case previously relaunched silently on the
        # OLD version with no diagnostic, surfacing only as a confusing
        # "already attempted an update … not retrying" message on the NEXT
        # manual start. `uv[0]` (cmd[0]) is the same absolute uv path used for
        # the upgrade itself. Best-effort only: any failure to even RUN this
        # check just skips the extra log line — it must never block the
        # relaunch below.
        uv_exe = cmd[0]
        version_check = ""
        if target_version:
            # The mismatch log line interpolates a LIVE PowerShell variable
            # ($ver, parsed moments earlier from uv's own output) in the
            # MIDDLE of an otherwise Python-injected string. `Log <a> + <b>`
            # (two separate _ps_quote()'d strings joined with `+` OUTSIDE the
            # call) is NOT string concatenation in PowerShell's bareword-call
            # syntax — it calls `Log <a>` and silently discards `+ <b>` as an
            # unused expression, dropping the actual detected version from
            # the log (found via a real pwsh parse+execute check, 2026-07-29:
            # the message logged was truncated exactly at that boundary).
            # Fix: escape the two STATIC halves with the same three
            # replacements _ps_quote() applies (backtick, then double-quote,
            # then $), and hand-assemble ONE double-quoted PowerShell string
            # with $ver left live in the middle — mirrors how $_/$p.ExitCode
            # are already interpolated live elsewhere in this same script.
            def _ps_escape_static(s: str) -> str:
                return s.replace("`", "``").replace('"', '`"').replace("$", "`$")

            _mismatch_prefix = _ps_escape_static(
                f"version check: expected {target_version} but 'uv tool list' reports "
            )
            _mismatch_suffix = _ps_escape_static(
                " -- upgrade did NOT converge (stale index cache? pinned receipt? "
                "partial failure?). Relaunching whatever is actually on disk."
            )
            mismatch_log_expr = f'"{_mismatch_prefix}$ver{_mismatch_suffix}"'
            version_check = textwrap.dedent(f"""
                try {{
                    $installed = & {_ps_quote(uv_exe)} tool list 2>$null |
                        Select-String -Pattern '^corvinos\\s+v?([0-9][^\\s]*)'
                    if ($installed) {{
                        $ver = $installed.Matches[0].Groups[1].Value
                        if ($ver -eq {_ps_quote(target_version)}) {{
                            Log {_ps_quote(f"version check: now on {target_version} -- converged")}
                        }} else {{
                            Log {mismatch_log_expr}
                        }}
                    }}
                }} catch {{
                    Log "version check skipped (uv tool list failed): $_"
                }}
            """)
        script = textwrap.dedent(f"""
            $ErrorActionPreference = "Continue"
            $log = {_ps_quote(str(log_path))}
            function Log($m) {{ Add-Content -Path $log -Value "$(Get-Date -Format o) $m" }}
            Log {_ps_quote(f"waiting for corvin-serve (pid {pid}) to exit")}
            while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{
                Start-Sleep -Milliseconds 400
            }}
            Log {_ps_quote(f"pid {pid} exited -- running upgrade: {cmd_str}")}
            $upgradeOk = $true
            try {{
                $p = Start-Process -FilePath {_ps_quote(cmd[0])} `
                    -ArgumentList {_ps_array_literal(cmd[1:])} `
                    -WindowStyle Hidden -Wait -PassThru
                if ($p.ExitCode -ne 0) {{
                    $upgradeOk = $false
                    Log {_ps_quote(f"upgrade FAILED (exit code below) -- relaunching the CURRENT (unupgraded) install so the service does not go down. Run manually to retry: {cmd_str}")}
                    Log "exit code: $($p.ExitCode)"
                }}
            }} catch {{
                $upgradeOk = $false
                Log {_ps_quote(f"upgrade FAILED to launch (exception below) -- relaunching the CURRENT (unupgraded) install so the service does not go down. Run manually to retry: {cmd_str}")}
                Log "exception: $_"
            }}
            if ($upgradeOk) {{
                Log {_ps_quote(f"upgrade command exited 0 -- relaunching: {relaunch_str}")}
                {version_check.strip()}
            }} else {{
                Log {_ps_quote(f"relaunching regardless of upgrade outcome (availability invariant): {relaunch_str}")}
            }}
            try {{
                Start-Process -FilePath {_ps_quote(relaunch_argv[0])} `
                    -ArgumentList {_ps_array_literal(relaunch_argv[1:])} `
                    -WindowStyle Hidden
                Log "relaunch dispatched"
            }} catch {{
                Log {_ps_quote(f"relaunch FAILED (exception below) -- corvin-serve is DOWN. Run manually: {relaunch_str}")}
                Log "exception: $_"
                exit 1
            }}
        """).strip()
        # utf-8-sig (BOM): Windows PowerShell 5.1 parses BOM-less files as
        # ANSI -- any non-ASCII char in an embedded absolute path (umlaut
        # user name) mojibakes and the detached updater targets broken
        # paths, leaving the server down after the handoff exit.
        script_path.write_text(script, encoding="utf-8-sig")

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


def _pypi_version_is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` is a strictly newer release than ``current``.

    Prefers ``packaging.version`` (a real dependency) for full PEP 440
    semantics; falls back to a numeric-tuple compare if it is somehow
    unavailable. On any parse failure, returns False — never upgrade on an
    ambiguous comparison, since the failure mode we are guarding against is a
    silent DOWNGRADE.
    """
    try:
        from packaging.version import Version  # noqa: PLC0415
        return Version(latest) > Version(current)
    except Exception:  # noqa: BLE001
        def _parts(v: str) -> tuple[int, ...]:
            out: list[int] = []
            for chunk in v.split("."):
                num = "".join(ch for ch in chunk if ch.isdigit())
                out.append(int(num) if num else 0)
            return tuple(out)
        try:
            return _parts(latest) > _parts(current)
        except Exception:  # noqa: BLE001
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

    # INST-2 / WA-2 / WA-3: when launched by the install.ps1 supervisor, that
    # supervisor already runs `uv tool upgrade` ONCE per logon, before its
    # restart loop. An in-process self-update handoff here would fight it: the
    # supervisor relaunches serve 5s after any exit and counts that exit against
    # its 5-per-300s crash budget, but the detached updater cannot replace the
    # locked venv within 5s — so the handoff merely burns restart budget and can
    # loop. Defer entirely to the supervisor's single per-logon upgrade.
    if os.environ.get("CORVIN_SUPERVISED") == "1":
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
        # Only upgrade when PyPI's reported version is STRICTLY NEWER than the
        # installed one. A bare ``latest != current`` check (the old logic)
        # DOWNGRADES on any transient PyPI CDN lag — where the JSON index still
        # reports the previous release for a few minutes right after an upload —
        # or permanently if a newer release is ever yanked. Observed live: a
        # fresh 0.10.28 install auto-"upgraded" to 0.10.27 on first boot,
        # un-doing the release it had just installed. Version-tuple compare, so
        # equal AND older both mean "do nothing".
        if not _pypi_version_is_newer(latest, current):
            # I2: when the convergence marker names the NOW-CURRENT version, a
            # Windows self-update handoff just completed (the upgrade ran in a
            # detached script after the previous process exited — this is the
            # first start of the upgraded install). The rebuilt venv may have
            # lost pip-injected playwright, and pre-0.10.48 receipts never got
            # Chromium — re-ensure browser provisioning, non-blocking.
            try:
                if _update_marker_path().read_text(encoding="utf-8").strip() == current:
                    _provision_browser_after_upgrade()
            except Exception:  # noqa: BLE001 — no marker / unreadable → nothing to do
                pass
            _clear_update_convergence_marker()
            print(f"up to date ({current})")
            return False
        # Step 2: a genuinely newer version exists — attempt upgrade with the
        # command that matches this install flavour (uv tool vs pip).
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
            # INST-1b convergence guard: refuse a SECOND handoff for the same
            # target version. If we already handed off for `latest` and the
            # relaunched process is STILL on `current`, the upgrade didn't take
            # — handing off again would relaunch → see `latest` → hand off →
            # loop forever. Fail safe: keep running the current version.
            if not _update_convergence_ok(latest):
                print(
                    f"\n  ⚠ already attempted an update to {latest} but still on "
                    f"{current} — not retrying to avoid a relaunch loop. "
                    f"Run manually:\n    {manual}"
                )
                return False
            print("handing off to background updater …", end=" ", flush=True)
            if _spawn_windows_self_updater(cmd, relaunch_argv, target_version=latest):
                # M3: arm the convergence guard only NOW — after the handoff
                # actually started. A spawn that failed below leaves no marker
                # and is retried on the next start.
                _record_update_attempt(latest)
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
            # I2: the upgrade rebuilt the tool venv from the receipt — make
            # sure the browser stack survived it (background, fail-soft).
            _provision_browser_after_upgrade()
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
    """Print a one-time disclosure about the anonymous activity telemetry.

    Two channels count how many instances are active, both opt-out (default ON):
      * a daily ping — installed version + a pseudonymous instance ID, and
      * a lightweight presence heartbeat every 5 minutes while the server runs,
        with an EMPTY body (no data beyond the same pseudonymous auth headers).
    Neither transmits personal data. A single opt-out
    (``telemetry.ping_enabled false``) disables BOTH — the heartbeat re-checks
    ``ping_enabled`` on every send. Shown exactly once per installation.
    """
    try:
        if _TELEMETRY_NOTICE_FILE.exists():
            return
        print(
            "\n  CorvinOS sends anonymous telemetry to count active instances:\n"
            "    - a daily ping (installed version + a pseudonymous instance ID), and\n"
            "    - a lightweight presence heartbeat (empty body) every 5 minutes\n"
            "      while the server is running.\n"
            "  No personal data is included in either.\n"
            "  To opt out of both: corvin config set telemetry.ping_enabled false\n"
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


def _seed_builtin_tools() -> None:
    """Seed the ADR-0191 builtin MCP tools into the mcp_manager catalog.

    The gateway's app.py lifespan already does this on the systemd/gateway
    path — but corvin-serve uses corvin_console.standalone, which has no
    FastAPI lifespan, so on the primary pip/uv install path the hook never
    ran and a fresh install had NO image generation despite ADR-0191's
    zero-config promise (verified on a real fresh venv + fresh HOME: the
    catalog stayed empty after a full corvin-serve boot). Same failure
    class as the startup-ping finding above. Importing corvin_console
    first is load-bearing: its _operator_bootstrap puts the vendored
    operator/ subtrees (wheel install) or the repo operator/ paths
    (source tree) on sys.path so ``mcp_manager`` resolves in both modes.
    Fail-soft: seeding problems must never block the console from starting.
    """
    def _seed() -> None:
        try:
            import corvin_console  # noqa: F401,PLC0415 — sys.path bootstrap side effect
            from mcp_manager.seed_builtin import ensure_imagegen_zero_config  # noqa: PLC0415
            ensure_imagegen_zero_config("_default")
        except Exception:                                        # noqa: BLE001
            pass
        try:
            from mcp_manager.seed_builtin import ensure_corvin_browser  # noqa: PLC0415
            ensure_corvin_browser("_default")  # ADR-0193
        except Exception:                                        # noqa: BLE001
            pass
    threading.Thread(target=_seed, daemon=True).start()


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

    # A second instance on the same port used to be a real, live-observed
    # failure mode on Windows: the install.ps1 supervisor's own healthz
    # standby loop only protects ITS restart loop, but the Desktop shortcut
    # (install.ps1's "3c. Desktop shortcut" step) launches corvinos-serve
    # directly, with no port check anywhere in this function — so a user
    # double-clicking the Desktop icon while the auto-started supervised
    # console is already running spawned a SECOND uvicorn process. Windows'
    # default SO_REUSEADDR semantics (unlike POSIX) can let a second bind
    # to an already-listening port succeed instead of failing cleanly, so
    # this silently produced two live console processes instead of an
    # error — confusing, hard to diagnose, and exactly the kind of
    # "fragile on Windows" symptom this check closes at the one place
    # every launch path (Desktop shortcut, Scheduled Task supervisor,
    # manual `corvin-serve`) already goes through.
    if _console_already_running(host, port):
        print(f"  CorvinOS console is already running at {url} — opening it "
              "instead of starting a second instance.")
        if open_browser:
            _schedule_browser_open(url.rstrip("/") + open_path, delay=0.2)
        return 0

    _show_telemetry_notice_once()
    _fire_startup_ping()
    _start_heartbeat()
    _seed_builtin_tools()

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
