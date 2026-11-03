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


def _ensure_utf8_child_env() -> None:
    """Make every CHILD of a Corvin Python process run in Python UTF-8 mode.

    Windows only. Without PYTHONUTF8, open()/read_text()/subprocess text pipes
    default to the ANSI code page (cp1252 on a German Windows) while the same
    stores are written as UTF-8 by Linux, Node and the other Corvin processes.
    UTF-8 mode is fixed at interpreter start-up, so this cannot switch THIS
    process -- the launchers (install.ps1 / update.ps1 / the .vbs task
    wrappers) set it for the top-level process -- but the environment is
    inherited by every bridge, worker, engine and hook spawned from here.
    An explicit operator value (including PYTHONUTF8=0) always wins.
    """
    if sys.platform != "win32":
        return
    try:
        os.environ.setdefault("PYTHONUTF8", "1")
    except Exception:
        pass


def _install_fcntl_shim() -> None:
    """Give EVERY Corvin Python process on Windows a working ``fcntl.flock``.

    The corvin_core / forge / bridges shims only exist in a process once one of
    those packages was imported, but CLIs (corvin-a2a, corvin-flow, corvin ...),
    MCP servers (corvin_delegate), workers and scripts import modules such as
    license.quota_counter, infinite_session.event_store, skill_adapter or
    routing_ledger first: a bare ``import fcntl`` crashed them with
    ModuleNotFoundError, and the many ``try: import fcntl / except ImportError:
    fcntl = None`` sites silently dropped the cross-process lock that the
    console/gateway (which do have the shim) still took -> lost updates. This
    hook runs at interpreter start (corvinOS_path_fix.pth), so it precedes every
    such import. Same flock emulation and same 1 TiB sentinel byte as the other
    shims (they keep this module: they only install when ``fcntl`` is absent),
    so all lockers exclude each other. ctypes is loaded on the first flock only.
    No-op on POSIX and when an ``fcntl`` module is already present.
    """
    if sys.platform != "win32" or "fcntl" in sys.modules:
        return
    try:
        import errno
        import types

        api: list = []

        def _load():
            if not api:
                import ctypes
                import msvcrt
                from ctypes import wintypes

                class _Overlapped(ctypes.Structure):
                    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                                ("hEvent", wintypes.HANDLE)]

                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                            wintypes.DWORD, ctypes.POINTER(_Overlapped)]
                k32.LockFileEx.argtypes = argtypes
                k32.LockFileEx.restype = wintypes.BOOL
                k32.UnlockFileEx.argtypes = argtypes[:1] + argtypes[2:]
                k32.UnlockFileEx.restype = wintypes.BOOL
                api.append((ctypes, msvcrt, _Overlapped, k32))
            return api[0]

        def _win_flock(fd, operation):
            ctypes, msvcrt, overlapped_t, k32 = _load()
            if not isinstance(fd, int):
                fd = fd.fileno()
            handle = msvcrt.get_osfhandle(fd)

            def _ov():
                ov = overlapped_t()
                ov.Offset, ov.OffsetHigh = 0, 1 << 8  # 1 << 40 == the other shims' sentinel
                return ov

            if not k32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_ov())):
                err = ctypes.get_last_error()
                if err != 158:  # ERROR_NOT_LOCKED
                    raise ctypes.WinError(err)
            if operation & 8:  # LOCK_UN
                return None
            flags = (0x2 if operation & 2 else 0) | (0x1 if operation & 4 else 0)
            if not k32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_ov())):
                err = ctypes.get_last_error()
                if err in (33, 997):  # ERROR_LOCK_VIOLATION / ERROR_IO_PENDING
                    raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
                raise ctypes.WinError(err)
            return None

        def lockf(fd, cmd, *_range):
            return _win_flock(fd, cmd)

        def _noop(*_a, **_k):
            return 0

        m = types.ModuleType("fcntl")
        m.LOCK_SH, m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 1, 2, 4, 8
        m.F_GETLK, m.F_SETLK, m.F_SETLKW = 5, 6, 7
        m.F_GETFD, m.F_SETFD, m.F_GETFL, m.F_SETFL = 1, 2, 3, 4
        m.flock, m.lockf, m.fcntl, m.ioctl = _win_flock, lockf, _noop, _noop
        m._corvin_win_shim = True
        sys.modules["fcntl"] = m
    except Exception:
        pass


_RMTREE_MARK = "_corvin_readonly_rmtree"


def _install_rmtree_readonly() -> None:
    """Make ``shutil.rmtree`` delete read-only files on Windows.

    On NTFS ``chmod 0o444`` (audit_sealer sealed segments, sandbox jail files)
    and every git object/pack file carry the READ-ONLY attribute, and
    ``os.unlink``/``rmdir`` then raise PermissionError (WinError 5) where POSIX
    deletes happily (directory permissions decide there). Erasure (GDPR),
    tenant removal, plugin/skill/package uninstalls and git-checkout cleanups
    all use plain ``shutil.rmtree``. Without an explicit error handler the
    default now clears the attribute and retries once; callers passing
    ``ignore_errors``/``onerror``/``onexc`` keep their behaviour. Idempotent;
    no-op on POSIX.
    """
    if sys.platform != "win32":
        return
    try:
        import shutil
        import stat

        original = shutil.rmtree
        if getattr(original, _RMTREE_MARK, False):
            return

        def _retry(func, path, _exc):
            if not isinstance(_exc, PermissionError):
                raise _exc
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)

        if sys.version_info >= (3, 12):
            def rmtree(path, ignore_errors=False, onerror=None, *, onexc=None, dir_fd=None):
                if not ignore_errors and onerror is None and onexc is None:
                    onexc = _retry
                return original(path, ignore_errors, onerror, onexc=onexc, dir_fd=dir_fd)
        else:  # 3.11: onerror(func, path, exc_info)
            def rmtree(path, ignore_errors=False, onerror=None, *, dir_fd=None):
                if not ignore_errors and onerror is None:
                    onerror = lambda f, p, ei: _retry(f, p, ei[1])  # noqa: E731
                return original(path, ignore_errors, onerror, dir_fd=dir_fd)

        setattr(rmtree, _RMTREE_MARK, True)
        rmtree.avoids_symlink_attacks = getattr(original, "avoids_symlink_attacks", False)
        rmtree.__doc__ = original.__doc__
        shutil.rmtree = rmtree
    except Exception:
        pass


_READ_RETRY_MARK = "_corvin_read_retry"
_READ_RETRY_SECONDS = 0.3


def _install_read_retry() -> None:
    """Retry a read-only ``open()`` that fails transiently on Windows.

    A writer's ``os.replace`` (temp file -> target) briefly makes the target
    unopenable: a concurrent reader gets PermissionError (errno 13) although the
    file is fine a few milliseconds later (measured: ~16% of reads against a
    tight replace loop). The console reads registries / settings / json stores
    on every request and many readers map any OSError to "empty/default", which
    the next writer then persists. Only pure read modes are retried, only for
    PermissionError, only while the path still exists and is not a directory,
    for at most ``_READ_RETRY_SECONDS``; a real ACL denial surfaces after that.
    Idempotent; no-op on POSIX.
    """
    if sys.platform != "win32":
        return
    try:
        import builtins
        import io
        import time

        original = builtins.open
        if getattr(original, _READ_RETRY_MARK, False):
            return

        def open(file, mode="r", *args, **kwargs):  # noqa: A001 - mirrors builtins.open
            try:
                return original(file, mode, *args, **kwargs)
            except PermissionError:
                if (not isinstance(file, (str, bytes, os.PathLike)) or not isinstance(mode, str)
                        or any(c in mode for c in "wax+")
                        or kwargs.get("opener") is not None or os.path.isdir(file)
                        or not os.path.exists(file)):
                    raise
            deadline = time.monotonic() + _READ_RETRY_SECONDS
            delay = 0.002
            while True:
                time.sleep(delay)
                delay = min(delay * 2, 0.02)
                try:
                    return original(file, mode, *args, **kwargs)
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise

        setattr(open, _READ_RETRY_MARK, True)
        open.__doc__ = original.__doc__
        builtins.open = open
        io.open = open
    except Exception:
        pass


_ensure_utf8_child_env()
_install_fcntl_shim()
_install_rmtree_readonly()
_install_read_retry()
_ensure_scripts_on_path()
