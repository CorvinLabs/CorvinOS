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
    if os.name == "nt":
        # bridge_manager.py has no persistent daemon to stop — processes are
        # children of the foreground Python process and die when it exits.
        return
    bridge_sh = _find_bridge_sh()
    if bridge_sh:
        subprocess.run(["bash", str(bridge_sh), "stop"], capture_output=True)
