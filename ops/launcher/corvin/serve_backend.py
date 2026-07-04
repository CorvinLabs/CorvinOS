"""Native serve backend — runs the CorvinOS console via uvicorn directly.

Used by ``corvin serve`` and as a fallback in ``corvin start`` when Docker
is not available.  No container runtime needed; only Python + the
``corvinOS[console]`` extras (FastAPI, uvicorn, ...).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
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


def maybe_pypi_autoupdate() -> None:
    """Run pip install --upgrade corvinos if auto_update is enabled (default on).

    Best-effort — never blocks or fails startup. Reads
    ~/.config/corvin-launcher/config.json for the auto_update flag.
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
        return

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
            return
        # Step 2: a newer version exists — attempt upgrade.
        print(f"upgrading {current} → {latest} …", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", f"corvinos=={latest}", "--quiet"],
            capture_output=True,
            timeout=120,
            text=True,
        )
        if result.returncode == 0:
            print("done — restart corvin-serve to apply")
        else:
            # pip failed (UAC, network, read-only env, …) — tell the user the
            # exact command rather than silently continuing.
            print(
                f"\n  ⚠ auto-upgrade failed. Run manually:\n"
                f"    pip install corvinos=={latest}"
            )
    except subprocess.TimeoutExpired:
        print("(timed out — continuing)")
    except Exception:
        print("(update check skipped)")


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
    """Send the opt-out activity ping in a daemon thread at serve startup.

    corvin-serve uses corvin_console.standalone which has no FastAPI lifespan
    and therefore never starts the boot-healer background task. Without this
    direct call ping_if_due() would never run for pip-install deployments.
    Fail-soft: any exception is silently swallowed — startup must never block.
    """
    def _ping() -> None:
        try:
            import time as _t                                    # noqa: PLC0415
            _t.sleep(6)          # wait for uvicorn to finish binding
            from corvin_console.aco.htrace_uploader import ping_if_due  # noqa: PLC0415
            from forge import paths as _p                        # noqa: PLC0415
            ping_if_due(_p.corvin_home())
        except Exception:                                        # noqa: BLE001
            pass

    threading.Thread(target=_ping, daemon=True).start()


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
