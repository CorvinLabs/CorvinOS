"""Windows PATH auto-fix for corvinOS CLI scripts.

Loaded automatically via ``corvinOS_path_fix.pth`` (placed in site-packages by
pip at install time).  On Windows only: detects the Python Scripts directory
that contains ``corvin-serve.exe`` and adds it to the Windows user PATH in the
registry so that ``corvin-serve`` works from CMD/PowerShell without the user
having to locate or edit the PATH manually.

After the first successful add the registry key already contains the directory,
so the check exits in < 1 ms on every subsequent Python start.  On all other
platforms this module is a no-op.

Must NOT raise — any exception is silently swallowed so a broken registry /
permission error never prevents Python from starting.
"""
from __future__ import annotations

import os
import sys


def _ensure_scripts_on_path() -> None:  # pragma: no cover — Windows-only
    if sys.platform != "win32":
        return
    try:
        import sysconfig

        # Candidate directories where pip may have placed corvin-serve.exe:
        # 1. Standard per-user scripts (APPDATA\Python\PythonXYZ\Scripts)
        # 2. Global scripts (C:\PythonXYZ\Scripts) — already on PATH usually
        # 3. Microsoft Store Python scripts (LOCALAPPDATA\Packages\...\Scripts)
        candidates: list[str] = []
        for scheme in ("nt_user", ""):
            try:
                p = sysconfig.get_path("scripts", scheme=scheme) if scheme else sysconfig.get_path("scripts")
                if p:
                    candidates.append(p)
            except Exception:
                pass

        # Derived candidates — sysconfig's schemes miss Microsoft Store Python,
        # whose pip-installed scripts land under
        # %LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.x_...\
        # LocalCache\local-packages\Python3xx\Scripts. Derive that (and the
        # interpreter-adjacent + user-site Scripts) directly so the PATH hook
        # actually finds corvin-serve.exe on Store Python.
        try:
            candidates.append(os.path.join(os.path.dirname(sys.executable), "Scripts"))
        except Exception:
            pass
        try:
            # __file__ = ...\<python-root>\site-packages\corvinOS\_win_path_fix.py
            _sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # site-packages
            candidates.append(os.path.join(os.path.dirname(_sp), "Scripts"))   # <python-root>\Scripts
        except Exception:
            pass
        try:
            import site as _site
            _usp = _site.getusersitepackages()
            if _usp:
                candidates.append(os.path.join(os.path.dirname(_usp), "Scripts"))
        except Exception:
            pass

        # Deduplicate while preserving order.
        _seen: set[str] = set()
        candidates = [c for c in candidates
                      if c and not (c.lower() in _seen or _seen.add(c.lower()))]

        # Any of these executables being present means pip placed scripts here.
        _CORVIN_EXES = (
            "corvin.exe",
            "corvin-serve.exe",
            "corvin-install.exe",
        )

        scripts_dir: str | None = None
        for c in candidates:
            if any(os.path.isfile(os.path.join(c, exe)) for exe in _CORVIN_EXES):
                scripts_dir = c
                break

        if not scripts_dir:
            return  # no corvin script found — nothing to register

        # --- 1. Fix for the current process ---
        path_env = os.environ.get("PATH", "")
        path_dirs_lower = {d.lower() for d in path_env.split(os.pathsep)}
        if scripts_dir.lower() not in path_dirs_lower:
            os.environ["PATH"] = scripts_dir + os.pathsep + path_env

        # --- 2. Persist to Windows user PATH in registry ---
        try:
            import winreg  # type: ignore[import]
            # KEY_QUERY_VALUE to read, KEY_SET_VALUE to write; reserved MUST be 0.
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment",
                0,  # reserved — must be 0
                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
            )
            try:
                existing, _ = winreg.QueryValueEx(key, "PATH")
            except OSError:  # FileNotFoundError / value absent
                existing = ""

            existing_lower = existing.lower()
            if scripts_dir.lower() not in existing_lower:
                new_path = (existing.rstrip(";") + ";" + scripts_dir) if existing else scripts_dir
                winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new_path)
                winreg.CloseKey(key)
                # Notify running processes about the PATH change (best-effort).
                try:
                    import ctypes
                    result = ctypes.c_long(0)
                    ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
                        0xFFFF,   # HWND_BROADCAST
                        0x001A,   # WM_SETTINGCHANGE
                        0,
                        "Environment",
                        0x0002,   # SMTO_ABORTIFHUNG
                        5000,
                        ctypes.byref(result),
                    )
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


_ensure_scripts_on_path()
