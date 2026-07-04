"""Web Console setup: Python dependencies, frontend build, and server start."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .dependencies import pip_install as _pip_install

# All paths derived relative to repo root — never hardcoded user homes.
_CONSOLE_DEPS = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pyyaml>=6.0",
    "pydantic>=2.4",
    "httpx>=0.27",
    "PyJWT[crypto]>=2.8",
    "cryptography>=42",
    "anthropic>=0.25",
    "openai>=1.0",
    "python-multipart>=0.0.6",
]

_PYTHONPATH_DIRS = [
    "core/console",
    "core/gateway",
    "core/license",
    "core/compliance",
    "operator/forge",
    "operator/skill-forge",
]

_PORT = 8765


def install_python_deps() -> bool:
    """Install Web Console Python dependencies into the current environment."""
    print("\n[Console] Installing Python dependencies...")
    ok = _pip_install(*_CONSOLE_DEPS)
    if ok:
        print("✓ Console Python dependencies installed")
    else:
        print(f"✗ Could not install console dependencies.")
        print(f"  Manual: pip install {' '.join(_CONSOLE_DEPS)}")
    return ok


def build_frontend(repo_root: Path) -> bool:
    """Build the React frontend (npm install + npm run build).

    All paths derived from repo_root — no hardcoded user directories.
    Falls back gracefully if web-next is not available (pre-built wheels).
    """
    webnext_dir = repo_root / "core" / "console" / "corvin_console" / "web-next"
    dist_dir = webnext_dir / "dist" / "index.html"

    # Check if frontend is already built (wheel install)
    if dist_dir.exists():
        print(f"✓ Frontend already built (from wheel/distribution)")
        return True

    print(f"\n[Console] Building frontend at {webnext_dir} ...")

    if not webnext_dir.exists():
        print(f"⚠ web-next directory not found — this is normal for pre-built wheels")
        print(f"  The web console will work with the pre-built frontend")
        return True

    if not (webnext_dir / "package.json").exists():
        print(f"⚠ package.json not found — skipping build")
        return True

    npm_cmd = _find_npm()
    if not npm_cmd:
        print("⚠ npm not found — install Node.js 20+ and re-run the installer")
        print(f"  Or build manually:")
        print(f"    cd {webnext_dir}")
        print(f"    npm install && npm run build")
        return False

    # npm install (always run to keep deps in sync)
    print("  Running npm install...")
    result = subprocess.run([npm_cmd, "install"], cwd=webnext_dir, check=False)
    if result.returncode != 0:
        print(f"✗ npm install failed")
        return False

    # npm run build
    print("  Running npm run build...")
    result = subprocess.run([npm_cmd, "run", "build"], cwd=webnext_dir, check=False)
    if result.returncode != 0:
        print("✗ npm run build failed")
        print(f"  Fix manually:")
        print(f"    cd {webnext_dir} && npm install && npm run build")
        return False

    index_html = webnext_dir / "dist" / "index.html"
    if index_html.exists():
        print(f"✓ Frontend built — {webnext_dir / 'dist'}")
        return True

    print("⚠ Build completed but dist/index.html is missing — something went wrong")
    print(f"  Fix manually: cd {webnext_dir} && npm install && npm run build")
    return False


def start_server(repo_root: Path) -> bool:
    """Start uvicorn for the Web Console in the background.

    PYTHONPATH is set from repo_root so all modules resolve correctly.
    Returns True when port 8765 is accepting connections.
    """
    print(f"\n[Console] Starting Web Console server on port {_PORT}...")

    # Kill any existing process on this port
    _kill_port(_PORT)

    # Build PYTHONPATH from repo_root (no hardcoded paths)
    sep = ";" if sys.platform == "win32" else ":"
    pythonpath = sep.join(str(repo_root / d) for d in _PYTHONPATH_DIRS)

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pythonpath + (sep + existing if existing else "")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "corvin_gateway.app:app",
            "--host", "0.0.0.0",
            "--port", str(_PORT),
            "--log-level", "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 15 s for the port to accept connections
    for _ in range(30):
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"✗ uvicorn exited immediately (rc={proc.returncode})")
            print(f"  Manual start:")
            print(f"    PYTHONPATH={pythonpath} \\")
            print(f"    python -m uvicorn corvin_gateway.app:app --host 0.0.0.0 --port {_PORT}")
            return False
        if _port_open("127.0.0.1", _PORT):
            print(f"✓ Web Console running on http://0.0.0.0:{_PORT}")
            print(f"  URL: http://localhost:{_PORT}/console/")
            return True

    print(f"⚠ Web Console did not start within 15 s on port {_PORT}")
    return False


# ── helpers ────────────────────────────────────────────────────────────────

def _find_npm() -> str | None:
    """Return the path to npm, falling back to nvm-sourced lookup on Linux/macOS."""
    npm = shutil.which("npm")
    if npm:
        return npm

    if sys.platform == "win32":
        return None

    # npm installed via nvm but not yet on PATH — source nvm.sh and resolve
    nvm_sh = Path.home() / ".nvm" / "nvm.sh"
    if nvm_sh.exists():
        result = subprocess.run(
            f"source {nvm_sh} && which npm",
            shell=True, executable="/bin/bash",
            capture_output=True, text=True, check=False,
        )
        npm_bin = result.stdout.strip()
        if npm_bin and Path(npm_bin).exists():
            node_dir = str(Path(npm_bin).parent)
            if node_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = node_dir + ":" + os.environ.get("PATH", "")
            return npm_bin

    return None


def _port_open(host: str, port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False


def _kill_port(port: int) -> None:
    """Best-effort kill of any process listening on port."""
    if sys.platform == "win32":
        # netstat -ano lists PID in the last column for LISTENING entries
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, check=False,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, check=False,
                )
    elif shutil.which("lsof"):
        result = subprocess.run(
            ["lsof", "-t", "-i", f":{port}"],
            capture_output=True, text=True, check=False,
        )
        for pid in result.stdout.strip().splitlines():
            subprocess.run(["kill", pid], check=False)
    elif shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{port}/tcp"], check=False)
    time.sleep(0.5)
