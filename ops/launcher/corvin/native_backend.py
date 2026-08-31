"""Native backend — uses bridge_manager.py (Windows) or bridge.sh (Linux/macOS).

bridge_manager.py is a pure-Python cross-platform launcher that replaces the
bash-based bridge.sh on Windows. It auto-installs Node.js via winget or a
direct binary download from nodejs.org, so no manual installation is needed.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import config as cfg

_BRIDGE_CANDIDATES = [
    Path(os.environ.get("CORVIN_REPO", "")) / "operator" / "bridges",
    # Source-tree location relative to THIS launcher file (ops/launcher/corvin/
    # native_backend.py → repo root = parents[3]); replaces a baked-in personal
    # ~/projects/CorvinOS path that only worked on one dev machine (path-audit #LOW7).
    Path(__file__).resolve().parents[3] / "operator" / "bridges",
    Path("/opt/corvin-repo/operator/bridges"),
]


def _find_bridges_dir() -> Optional[Path]:
    for p in _BRIDGE_CANDIDATES:
        if p.is_dir():
            return p
    return None


def _find_bridge_sh() -> Optional[Path]:
    d = _find_bridges_dir()
    if d:
        sh = d / "bridge.sh"
        if sh.exists():
            return sh
    return None


def _find_bridge_manager() -> Optional[Path]:
    d = _find_bridges_dir()
    if d:
        mgr = d / "bridge_manager.py"
        if mgr.exists():
            return mgr
    # pip install: bridge_manager.py is vendored inside corvin_console
    try:
        import importlib.util as _ilu
        spec = _ilu.find_spec("corvin_console")
        if spec and spec.origin:
            p = Path(spec.origin).parent / "_vendor" / "operator" / "bridges" / "bridge_manager.py"
            if p.exists():
                return p
    except Exception:
        pass
    return None


def is_available() -> bool:
    """Return True when a bridge launcher is available for this platform."""
    if os.name == "nt":
        # On Windows we use bridge_manager.py (no bash needed)
        return _find_bridge_manager() is not None
    # POSIX: bridge.sh (source tree) preferred; on a wheel install only the
    # vendored bridge_manager.py exists — without this fallback a Linux/macOS
    # pip install had NO way to start the bridges at all.
    return _find_bridge_sh() is not None or _find_bridge_manager() is not None


def start(foreground: bool = True) -> int:
    conf = cfg.load()
    env = os.environ.copy()
    env["CORVIN_OLLAMA_BASE_URL"] = conf["ollama_url"]
    env["CORVIN_HERMES_MODEL"] = conf["model"]
    if conf.get("bridge"):
        env[f"CORVIN_BRIDGE_{conf['bridge'].upper()}"] = "true"

    if os.name == "nt":
        # Windows: pure-Python launcher (no bash required)
        mgr = _find_bridge_manager()
        if not mgr:
            raise RuntimeError(
                "bridge_manager.py not found. "
                "Install CorvinOS from source for bridge support."
            )
        result = subprocess.run([sys.executable, str(mgr), "fg"], env=env)
        return result.returncode

    # Linux / macOS: bridge.sh fg (source tree), else the vendored
    # cross-platform bridge_manager.py (wheel install).
    bridge_sh = _find_bridge_sh()
    if bridge_sh:
        result = subprocess.run(["bash", str(bridge_sh), "fg"], env=env)
        return result.returncode
    mgr = _find_bridge_manager()
    if not mgr:
        raise RuntimeError("native backend: neither bridge.sh nor bridge_manager.py found")
    result = subprocess.run([sys.executable, str(mgr), "fg"], env=env)
    return result.returncode


def stop() -> None:
    """Stop whatever native daemon this instance is running as.

    2026-08-03 fix: this used to call ``bridge.sh stop`` on POSIX — not a
    real subcommand (bridge.sh only knows ``up|down|status|restart|
    install-units|logs|tail|fg|console|doctor``), so every call silently
    no-op'd and ``corvin gateway stop`` never actually stopped anything on
    the most common native install path. On Windows it was an explicit
    no-op, leaving the DEFAULT Stufe-1 login-autostart (``install.ps1`` /
    ADR-0184 — a Scheduled Task named ``CorvinOS-Console`` running
    ``corvin-supervisor.ps1``, which relaunches the console on crash) with
    no way to be stopped from this CLI at all.

    Two complementary mechanisms, both best-effort and never fatal:

    1. ``ops.launcher.service_entry._quiesce_stage1(stop_running=True)`` —
       the SAME cross-platform console-stop logic ``corvin-service`` already
       uses when handing off Stufe 1 → Stufe 2 (ADR-0184). Reused rather
       than re-implemented: it already gets the Windows Scheduled Task
       (``schtasks /end``), the macOS LaunchAgent (``launchctl bootout`` in
       the right GUI domain), and the Linux systemd user unit
       (``systemctl --user disable --now corvin-webui.service``) each
       right, including edge cases (SUDO_USER resolution, missing uid) this
       function would otherwise have to duplicate and could easily get
       subtly wrong. Covers the console on all three platforms.
    2. ``bridge.sh down`` (POSIX with systemd --user only) — additionally
       tears down every messaging-bridge channel unit and timer
       ``bridge.sh up`` started (``ALL_UNITS`` — Discord/Telegram/Slack/…
       plus the session-timeout/audit-verify/etc. timers), which
       ``_quiesce_stage1`` does not touch. On macOS / WSL2 without systemd
       this call itself prints a clear fallback message rather than
       silently doing nothing (bridge.sh's own ``require_systemd`` guard) —
       the console is still covered by step 1 above on that host.

    Windows bridge channels registered via ``bridge_manager.py``'s own
    per-channel autostart (``ensure_windows_autostart``) are NOT covered by
    either mechanism — a narrower, separate Scheduled Task per channel this
    function does not enumerate. The console (the primary, default-registered
    autostart target) is covered; a channel-specific gap is a known
    follow-up, not silently claimed as complete.
    """
    try:
        from ops.launcher.service_entry import _quiesce_stage1  # noqa: PLC0415
        _quiesce_stage1(stop_running=True)
    except Exception:  # noqa: BLE001 — best-effort; must never block the rest of stop()
        pass

    if os.name == "nt":
        return
    bridge_sh = _find_bridge_sh()
    if not bridge_sh:
        # No source-tree bridge.sh (e.g. a POSIX pip-wheel install without a
        # bash on PATH) — bridge_manager.py, the vendored fallback, has no
        # daemon-mode verb to stop either (only fg/ensure-node/doctor), so
        # there is nothing more this backend manages beyond the console
        # already handled above.
        return
    result = subprocess.run(
        ["bash", str(bridge_sh), "down"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Surface it — silently swallowing this (the old `capture_output=True`
        # with the result discarded) is exactly how the "stop" typo above
        # went unnoticed: a failed stop looked identical to a successful one.
        out = (result.stdout or "") + (result.stderr or "")
        if out.strip():
            print(out.strip())
