"""OS and platform detection."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum


class OS(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WSL = "wsl"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class PkgMgr(str, Enum):
    APT = "apt"
    DNF = "dnf"
    PACMAN = "pacman"
    BREW = "brew"
    WINGET = "winget"
    NONE = "none"


@dataclass
class PlatformInfo:
    os_kind: OS = OS.UNKNOWN
    pkg_mgr: PkgMgr = PkgMgr.NONE
    has_systemd: bool = False
    brew_prefix: str = ""
    arch: str = ""
    warnings: list[str] = field(default_factory=list)


def detect() -> PlatformInfo:
    """Detect OS, package manager, and capabilities."""
    info = PlatformInfo()
    info.arch = _arch()

    if sys.platform == "win32":
        info.os_kind = OS.WINDOWS
        if _cmd_exists("winget"):
            info.pkg_mgr = PkgMgr.WINGET
        return info

    if sys.platform == "darwin":
        info.os_kind = OS.MACOS
        info.pkg_mgr = _detect_brew(info)
        return info

    # Linux / WSL
    if _is_wsl():
        info.os_kind = OS.WSL
        info.warnings.append(
            "WSL has no audio device by default — TTS read-aloud will be silent. "
            "Voice notes sent to your phone via Discord/Telegram still work."
        )
    else:
        info.os_kind = OS.LINUX

    if _cmd_exists("apt"):
        info.pkg_mgr = PkgMgr.APT
    elif _cmd_exists("dnf"):
        info.pkg_mgr = PkgMgr.DNF
    elif _cmd_exists("pacman"):
        info.pkg_mgr = PkgMgr.PACMAN
    else:
        info.warnings.append(
            "No known package manager (apt/dnf/pacman) — "
            "you may need to install tools manually."
        )

    info.has_systemd = _has_systemd()
    return info


def pkg_install(info: PlatformInfo, *packages: str) -> bool:
    """Install system packages via the detected package manager.

    Returns True on success, False on failure.
    """
    if not packages:
        return True
    if info.pkg_mgr == PkgMgr.NONE:
        print(f"⚠ No package manager — install manually: {' '.join(packages)}")
        return False

    cmds: dict[PkgMgr, list[str]] = {
        PkgMgr.APT:    ["sudo", "apt", "install", "-y"],
        PkgMgr.DNF:    ["sudo", "dnf", "install", "-y"],
        PkgMgr.PACMAN: ["sudo", "pacman", "-S", "--noconfirm"],
        PkgMgr.BREW:   ["brew", "install"],
        PkgMgr.WINGET: ["winget", "install", "--silent"],
    }
    base = cmds[info.pkg_mgr]

    if info.pkg_mgr == PkgMgr.APT:
        subprocess.run(["sudo", "apt", "update", "-qq"], check=False)

    result = subprocess.run(base + list(packages), check=False)
    return result.returncode == 0


# ── internals ──────────────────────────────────────────────────────────────

def _cmd_exists(name: str) -> bool:
    try:
        result = subprocess.run(
            ["which", name] if sys.platform != "win32" else ["where", name],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_wsl() -> bool:
    try:
        proc_version = open("/proc/version").read().lower()
        return "microsoft" in proc_version or "wsl" in proc_version
    except OSError:
        return False


def _has_systemd() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _detect_brew(info: PlatformInfo) -> PkgMgr:
    """Detect and source Homebrew, return PkgMgr.BREW or PkgMgr.NONE."""
    for prefix in ("/opt/homebrew", "/usr/local"):
        brew_bin = f"{prefix}/bin/brew"
        if os.path.isfile(brew_bin):
            # Source brew into PATH for this process
            try:
                result = subprocess.run(
                    [brew_bin, "--prefix"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                info.brew_prefix = result.stdout.strip()
                brew_path = f"{info.brew_prefix}/bin"
                if brew_path not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = brew_path + ":" + os.environ.get("PATH", "")
            except Exception:
                pass
            return PkgMgr.BREW
    return PkgMgr.NONE


def _arch() -> str:
    if sys.platform == "win32":
        import platform as _platform
        return _platform.machine().lower()  # e.g. "amd64" or "arm64"
    try:
        result = subprocess.run(["uname", "-m"], capture_output=True, text=True, check=False)
        return result.stdout.strip()
    except Exception:
        return ""
