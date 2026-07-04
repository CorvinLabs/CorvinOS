"""Dependency installation: Claude Code, Node.js, system tools, Python packages."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .platform import OS, PkgMgr, PlatformInfo, pkg_install


# ── Claude Code ────────────────────────────────────────────────────────────

CLAUDE_CRED_PATHS = [
    Path.home() / ".claude" / ".credentials.json",
    Path.home() / ".claude" / "credentials.json",
    Path.home() / ".config" / "claude" / ".credentials.json",
    Path.home() / ".config" / "claude" / "credentials.json",
]


def find_claude_creds() -> Path | None:
    for p in CLAUDE_CRED_PATHS:
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def ensure_claude_code(interactive: bool = True) -> bool:
    """Install Claude Code if missing. Returns True when available."""
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            # On Windows, .cmd wrappers need shell=True with a quoted string
            # command; passing a list with shell=True or without shell=True
            # both raise WinError 2 for .cmd files.
            if sys.platform == "win32":
                result = subprocess.run(
                    f'"{claude_bin}" --version',
                    capture_output=True, text=True, check=False,
                    shell=True,
                )
            else:
                result = subprocess.run(
                    [claude_bin, "--version"],
                    capture_output=True, text=True, check=False,
                )
            lines = result.stdout.strip().splitlines()
            version = lines[0] if lines else "unknown"
        except OSError:
            version = "unknown"
        print(f"✓ Claude Code already installed: {version}")
        return True

    print("⚠ Claude Code is not installed.")
    if interactive:
        answer = input("Install Claude Code now? [Y/n]: ").strip().lower() or "y"
        if answer.startswith("n"):
            print("⚠ Claude Code is required for bridges. Continuing without it.")
            return False

    print("  Installing Claude Code via official installer...")
    if sys.platform == "win32":
        ok = _install_claude_windows()
    else:
        result = subprocess.run(
            "curl -fsSL https://claude.ai/install.sh | bash",
            shell=True,
            check=False,
        )
        ok = result.returncode == 0

    if not ok:
        print("✗ Claude Code install failed.")
        return False

    # Extend PATH for this process
    import os
    if sys.platform == "win32":
        _extend_path_windows()
    else:
        for extra in (
            Path.home() / ".local" / "bin",
            Path.home() / ".claude" / "local",
        ):
            if extra.is_dir():
                os.environ["PATH"] = str(extra) + ":" + os.environ.get("PATH", "")

    # Extend PATH with every likely location before probing
    import glob as _glob
    home = Path.home()
    nvm_sh = home / ".nvm" / "nvm.sh"
    extras: list[Path] = [
        home / ".local" / "bin",
        home / ".claude" / "local",
        home / ".npm" / "bin",
    ]
    # Add every nvm-managed node bin dir (newest version first)
    for p in sorted(_glob.glob(str(home / ".nvm" / "versions" / "node" / "*" / "bin")),
                    reverse=True):
        extras.append(Path(p))

    import os as _os
    for d in extras:
        if d.is_dir() and str(d) not in _os.environ.get("PATH", ""):
            _os.environ["PATH"] = str(d) + ":" + _os.environ.get("PATH", "")

    if shutil.which("claude"):
        version = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, check=False,
        ).stdout.strip().splitlines()[0]
        print(f"✓ Claude Code installed: {version}")
        return True

    print("✗ Install completed but 'claude' is not on PATH.")
    print("  Re-open your terminal and run the installer again.")
    return False


def ensure_claude_login(interactive: bool = True) -> bool:
    """Verify Claude Code is logged in. Returns True when credentials found."""
    cred_path = find_claude_creds()
    if cred_path:
        print(f"✓ Claude Code logged in ({cred_path})")
        return True

    if not interactive:
        print("⚠ Claude Code credentials not found — skipping login in non-interactive mode.")
        return False

    print()
    print("  Claude Code is not logged in yet.")
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │  Open a NEW terminal window and run:                   │")
    print("  │                                                         │")
    print("  │      claude login                                       │")
    print("  │                                                         │")
    print("  │  Opens your browser for Anthropic OAuth.               │")
    print("  │  On headless / WSL: it prints a URL — open it.        │")
    print("  │  When done, come back here and press ENTER.            │")
    print("  └─────────────────────────────────────────────────────────┘")
    input("  Press ENTER once you have completed the login... ")

    cred_path = find_claude_creds()
    if cred_path:
        print(f"✓ Login confirmed ({cred_path})")
        return True

    print("⚠ Credentials not found — continuing anyway.")
    print("  Run 'claude login' in a separate terminal, then re-run the installer.")
    return False


# ── Node.js ────────────────────────────────────────────────────────────────

def ensure_node(info: PlatformInfo, interactive: bool = True) -> bool:
    """Ensure Node.js >= 20 AND npm are installed. Returns True when both available."""
    import os as _os

    node = shutil.which("node")
    if node:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=False,
        )
        ver = result.stdout.strip().lstrip("v")
        major = int(ver.split(".")[0]) if ver else 0
        if major >= 20:
            if shutil.which("npm"):
                print(f"✓ node v{ver} + npm")
                return True

            # node found but npm missing (common when installed via apt install nodejs)
            # Try to bring npm in via nvm first
            nvm_sh = Path.home() / ".nvm" / "nvm.sh"
            if nvm_sh.exists():
                r = subprocess.run(
                    f"source {nvm_sh} && which npm",
                    shell=True, executable="/bin/bash",
                    capture_output=True, text=True, check=False,
                )
                npm_bin = r.stdout.strip()
                if npm_bin and Path(npm_bin).exists():
                    npm_dir = str(Path(npm_bin).parent)
                    if npm_dir not in _os.environ.get("PATH", ""):
                        _os.environ["PATH"] = npm_dir + ":" + _os.environ.get("PATH", "")
                    if shutil.which("npm"):
                        print(f"✓ node v{ver} + npm (via nvm)")
                        return True

            # Still no npm — install it via the system package manager
            print(f"  node v{ver} found but npm is missing — installing npm...")
            if info.pkg_mgr == PkgMgr.APT:
                subprocess.run(["sudo", "apt-get", "install", "-y", "npm"], check=False)
            elif info.pkg_mgr == PkgMgr.DNF:
                subprocess.run(["sudo", "dnf", "install", "-y", "npm"], check=False)
            elif info.pkg_mgr == PkgMgr.BREW:
                subprocess.run(["brew", "install", "node"], check=False)
            if shutil.which("npm"):
                print(f"  ✓ npm installed")
                return True
            print("  ✗ npm still missing — install it manually: sudo apt install npm")
            return False

        print(f"⚠ node v{ver} is too old — need ≥ 20.")

    if interactive:
        answer = input("Install Node.js 20+? [Y/n]: ").strip().lower() or "y"
        if answer.startswith("n"):
            print("✗ Node.js is required for the bridge daemons.")
            return False

    if info.pkg_mgr == PkgMgr.WINGET:
        return _install_node_windows()

    if info.pkg_mgr == PkgMgr.BREW:
        result = subprocess.run(["brew", "install", "node@20"], check=False)
        subprocess.run(["brew", "link", "--overwrite", "node@20"], check=False)
        return result.returncode == 0

    # Linux / WSL: use nvm
    print("  Installing nvm (Node Version Manager)...")
    result = subprocess.run(
        "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash",
        shell=True,
        check=False,
    )
    if result.returncode != 0:
        print("✗ nvm install failed.")
        return False

    nvm_sh = Path.home() / ".nvm" / "nvm.sh"
    if not nvm_sh.exists():
        print("✗ nvm.sh not found after install.")
        return False

    result = subprocess.run(
        f"source {nvm_sh} && nvm install --lts && nvm alias default lts/*",
        shell=True,
        executable="/bin/bash",
        check=False,
    )
    if result.returncode != 0:
        return False

    # Bring the newly installed node into the current process's PATH
    import os
    result_path = subprocess.run(
        f"source {nvm_sh} && which node",
        shell=True, executable="/bin/bash",
        capture_output=True, text=True, check=False,
    )
    node_bin = result_path.stdout.strip()
    if node_bin:
        node_dir = str(Path(node_bin).parent)
        if node_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = node_dir + ":" + os.environ.get("PATH", "")

    return bool(shutil.which("node"))


# ── System tools ───────────────────────────────────────────────────────────

_LINUX_TOOLS: list[tuple[str, str]] = [
    ("ffmpeg",       "ffmpeg"),
    ("jq",           "jq"),
    ("espeak-ng",    "espeak-ng"),
    ("pdftotext",    "poppler-utils"),
    ("pandoc",       "pandoc"),
    ("paplay",       "pulseaudio-utils"),
    ("curl",         "curl"),
    ("gnupg",        "gnupg"),
]

_LINUX_TOOLS_DNF: dict[str, str] = {
    "espeak-ng": "espeak-ng",
    "pdftotext": "poppler-tools",
}

_MACOS_TOOLS: list[tuple[str, str]] = [
    ("ffmpeg",    "ffmpeg"),
    ("jq",        "jq"),
    ("pdftotext", "poppler"),
    ("pandoc",    "pandoc"),
]


def ensure_system_tools(info: PlatformInfo, interactive: bool = True) -> None:
    """Check and install required system tools."""
    if info.os_kind == OS.WINDOWS:
        print("  ℹ Skipping system tools on Windows — install ffmpeg/jq manually if needed.")
        return

    pairs = _LINUX_TOOLS if info.os_kind in (OS.LINUX, OS.WSL) else _MACOS_TOOLS
    missing_pkgs: list[str] = []
    for binary, pkg in pairs:
        if not shutil.which(binary):
            # On dnf some package names differ
            if info.pkg_mgr == PkgMgr.DNF and pkg in _LINUX_TOOLS_DNF:
                pkg = _LINUX_TOOLS_DNF[pkg]
            missing_pkgs.append(pkg)

    if not missing_pkgs:
        print("✓ All system tools present")
        return

    print(f"⚠ Missing system tools: {' '.join(missing_pkgs)}")
    if interactive:
        answer = input(f"Install via {info.pkg_mgr.value}? [Y/n]: ").strip().lower() or "y"
        if answer.startswith("n"):
            return

    ok = pkg_install(info, *missing_pkgs)
    if not ok:
        print("⚠ Some installs may have failed — you can re-run the installer later.")


# ── Python packages ────────────────────────────────────────────────────────

def _ensure_pip_available(python_exe: str) -> None:
    """Ensure pip is available, bootstrap with ensurepip if needed.

    Critical on uv-installed Python where pip might not be included.
    This is best-effort — failures are non-blocking since we fall back to venv.
    """
    result = subprocess.run(
        [python_exe, "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return  # pip is already available

    # pip is missing — try ensurepip
    result = subprocess.run(
        [python_exe, "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # ensurepip failed — log but don't crash, we'll fall back to venv
        pass


def pip_install(*packages: str, venv_python: Path | None = None) -> bool:
    """Install Python packages with virtualenv + PEP 668 awareness.

    Order:
    1. Ensure pip is available (ensurepip if needed)
    2. Inside a virtualenv → plain pip install (no flags needed)
    3. --user (standard system Python)
    4. --break-system-packages (PEP 668 hosts)
    5. dedicated venv at ~/.config/corvin-voice/venv (last resort)

    Returns True when install succeeds.
    """
    import os
    python = str(venv_python) if venv_python else sys.executable
    pkg_list = list(packages)

    # Ensure pip is available (critical on uv-installed Python)
    _ensure_pip_available(python)

    # Inside a virtualenv: plain install, no --user (not allowed in venvs)
    in_venv = (
        sys.prefix != sys.base_prefix
        or os.environ.get("VIRTUAL_ENV") is not None
    )
    if in_venv:
        result = subprocess.run(
            [python, "-m", "pip", "install", "--quiet"] + pkg_list,
            check=False,
        )
        if result.returncode == 0:
            return True
        print("✗ pip install failed inside virtualenv")
        return False

    # Attempt 1: normal --user install
    result = subprocess.run(
        [python, "-m", "pip", "install", "--user", "--quiet"] + pkg_list,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True

    # PEP 668 detection
    if "externally-managed" not in result.stderr and "PEP 668" not in result.stderr:
        print(f"⚠ pip install failed:\n{result.stderr.strip()}")
        return False

    print("  System Python is externally-managed (PEP 668). Trying alternatives...")

    # Attempt 2: --break-system-packages
    result2 = subprocess.run(
        [python, "-m", "pip", "install", "--user", "--quiet",
         "--break-system-packages"] + pkg_list,
        capture_output=True,
        text=True,
        check=False,
    )
    if result2.returncode == 0:
        print("  Installed via --break-system-packages")
        return True

    # Attempt 3: dedicated venv
    venv_dir = Path.home() / ".config" / "corvin-voice" / "venv"
    print(f"  Falling back to venv at {venv_dir} ...")
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    venv_create = subprocess.run(
        [python, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if venv_create.returncode != 0:
        print(f"✗ venv creation failed: {venv_create.stderr.strip()}")
        print("  On Debian/Ubuntu try: sudo apt install python3-venv")
        return False

    # Windows uses Scripts\pip.exe, Unix uses bin/pip
    venv_pip = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / ("pip.exe" if sys.platform == "win32" else "pip")
    result3 = subprocess.run(
        [str(venv_pip), "install", "--quiet"] + pkg_list,
        check=False,
    )
    if result3.returncode == 0:
        print(f"✓ Installed via venv at {venv_dir}")
        _persist_venv_python(venv_dir / "bin" / "python3")
        return True

    print("✗ All pip install strategies failed.")
    return False


def _persist_venv_python(venv_python: Path) -> None:
    """Write PY_BIN to service.env so the adapter uses the venv."""
    env_file = Path.home() / ".config" / "corvin-voice" / "service.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.touch()

    lines = [l for l in env_file.read_text().splitlines() if not l.startswith("PY_BIN=")]
    lines.append(f"PY_BIN={venv_python}")
    env_file.write_text("\n".join(lines) + "\n")
    if sys.platform != "win32":
        env_file.chmod(0o600)
    print(f"  PY_BIN={venv_python} written to service.env")


# ── Windows-specific helpers ───────────────────────────────────────────────

def _install_claude_windows() -> bool:
    """Install Claude Code on Windows — winget first, npm fallback."""
    import os

    # Attempt 1: winget (works on Windows 11 and updated Win 10)
    if shutil.which("winget"):
        print("  Trying: winget install Anthropic.ClaudeCode ...")
        r = subprocess.run(
            ["winget", "install", "--id", "Anthropic.ClaudeCode",
             "--silent", "--accept-package-agreements",
             "--accept-source-agreements"],
            check=False,
        )
        if r.returncode == 0:
            return True
        print("  ⚠ winget install failed — trying npm fallback...")

    # Attempt 2: npm global install (cross-platform, needs Node.js pre-installed)
    npm = shutil.which("npm")
    if npm:
        print("  Trying: npm install -g @anthropic-ai/claude-code ...")
        r = subprocess.run(
            ["npm", "install", "-g", "@anthropic-ai/claude-code"],
            check=False,
        )
        if r.returncode == 0:
            return True

    print("✗ Automatic install failed.")
    print("  Manual: https://claude.ai/download  →  install Claude.exe")
    print("  Or: winget install Anthropic.ClaudeCode")
    return False


def _install_node_windows() -> bool:
    """Install Node.js 20+ on Windows via winget."""
    print("  Installing Node.js via winget...")
    r = subprocess.run(
        ["winget", "install", "--id", "OpenJS.NodeJS.LTS",
         "--silent", "--accept-package-agreements",
         "--accept-source-agreements"],
        check=False,
    )
    if r.returncode == 0:
        print("  ✓ Node.js installed — you may need to restart the terminal")
        return True

    print("✗ winget install failed.")
    print("  Manual: https://nodejs.org/en/download  → Windows Installer (.msi)")
    return False


def _extend_path_windows() -> None:
    """Add common Windows install locations for Claude Code to PATH."""
    import os
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude",
        Path(os.environ.get("APPDATA", "")) / "npm",
        Path(os.environ.get("PROGRAMFILES", "")) / "Anthropic" / "Claude Code",
    ]
    sep = ";"
    current = os.environ.get("PATH", "")
    for p in candidates:
        if p.is_dir() and str(p) not in current:
            os.environ["PATH"] = str(p) + sep + current
            current = os.environ["PATH"]
