"""Portable fcntl shim — real ``fcntl`` on POSIX, a LockFileEx flock on Windows.

The shared/ modules use ONLY the advisory-file-lock family (``flock`` +
``LOCK_EX``/``LOCK_SH``/``LOCK_UN``/``LOCK_NB``) for per-file registry locking.
On Windows ``fcntl`` does not exist; a bare top-level ``import fcntl`` therefore
crashed mandatory layers at IMPORT time — including L19 bot-disclosure
(disclosure.py, EU AI Act Art. 50), the ADR-0156 Custom-Layer registry, consent
(L16 Phase 4), and the L36 erasure orchestrator — so the whole module failed to
load on Windows (security-audit 2026-06-25 #11/#12).

On Windows ``flock`` is emulated with real ``LockFileEx`` locks (``_win_flock``):
the bridge adapter, console, forge MCP server and CLI tools are separate
processes doing read-modify-write on these registries (quota, consent, roles,
process table ...), so the former no-op lock lost updates. If a Windows
``fcntl`` stand-in is already in ``sys.modules`` (corvin_core / forge shim) it is
re-exported. POSIX behaviour is unchanged (the real module is re-exported). Callers do ``from _compat_fcntl import fcntl``
and keep using ``fcntl.flock(fd, fcntl.LOCK_EX)`` verbatim.
"""
from __future__ import annotations

import errno as _errno

# --- real cross-process flock() for Windows ------------------------------------
# The console, the bridge adapter, the forge MCP server, hooks, CLI tools and the
# daemons are SEPARATE processes that read-modify-write the same registries,
# quota counters, ledgers and lock sidecars on every platform, Windows included.
# A no-op flock therefore lost updates there (see forge/security_events.py for
# the audit-chain fork it caused). This emulates flock() with LockFileEx on one
# byte far past any real EOF: Windows byte-range locks are MANDATORY, so locking
# real data (offset 0) would make the locked file unreadable even to its own
# process; a sentinel byte at 1 TiB serialises lockers without blocking normal
# reads/writes. The offset matches security_events' msvcrt sentinel, so both
# lock flavours exclude each other on the same file exactly like POSIX flock.
_WIN_LOCK_OFFSET = 1 << 40
_win_lock_api = None


def _win_lock_api_load():
    global _win_lock_api
    if _win_lock_api is None:
        try:
            import ctypes  # noqa: PLC0415 - Windows-only path
            import msvcrt  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

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
            _win_lock_api = (ctypes, msvcrt, _Overlapped, k32)
        except (ImportError, OSError, AttributeError):
            _win_lock_api = False  # simulated Windows on POSIX (tests): stay inert
    return _win_lock_api


def _win_flock(fd, operation):
    """``fcntl.flock`` for Windows: LOCK_SH/LOCK_EX/LOCK_UN, LOCK_NB raises
    ``BlockingIOError`` like the real one. Lock is per handle (= per open file
    description, as flock) and released when the file is closed."""
    api = _win_lock_api_load()
    if not api:
        return None
    ctypes, msvcrt, overlapped_t, k32 = api
    if not isinstance(fd, int):
        fd = fd.fileno()
    handle = msvcrt.get_osfhandle(fd)

    def _ov():
        ov = overlapped_t()
        ov.Offset = _WIN_LOCK_OFFSET & 0xFFFFFFFF
        ov.OffsetHigh = _WIN_LOCK_OFFSET >> 32
        return ov

    # flock converts a lock already held through this fd, and LOCK_UN on an
    # unlocked fd is not an error: drop any held lock first, ignore NOT_LOCKED.
    if not k32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_ov())):
        err = ctypes.get_last_error()
        if err != 158:  # ERROR_NOT_LOCKED
            raise ctypes.WinError(err)
    if operation & 8:  # LOCK_UN
        return None
    flags = 0x2 if operation & 2 else 0  # LOCK_EX -> LOCKFILE_EXCLUSIVE_LOCK
    if operation & 4:  # LOCK_NB -> LOCKFILE_FAIL_IMMEDIATELY
        flags |= 0x1
    if not k32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_ov())):
        err = ctypes.get_last_error()
        if err in (33, 997):  # ERROR_LOCK_VIOLATION / ERROR_IO_PENDING
            raise BlockingIOError(_errno.EAGAIN, "Resource temporarily unavailable")
        raise ctypes.WinError(err)
    return None


def _win_lockf(fd, cmd, *_range):
    """``fcntl.lockf`` (whole-file use only in this codebase) -> ``_win_flock``."""
    return _win_flock(fd, cmd)


try:  # Linux / macOS — use the real module unchanged.
    import fcntl as fcntl  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover - exercised only on Windows
    import sys as _sys
    import types as _types

    # (A shim already in sys.modules was returned by the import above.)
    # Register the stand-in for the rest of the process too. shared/ code
    # puts corvin_operator/forge/forge on sys.path and imports ``clag`` /
    # ``security_events`` as top-level modules, bypassing forge/__init__'s
    # shim -- their bare ``import fcntl`` then raised ImportError and the
    # fail-closed L19 CLAG gate refused every disclosure mark_seen on
    # Windows. Same shape as corvin_core/_wincompat.install().
    _m = _types.ModuleType("fcntl")
    _m.LOCK_SH, _m.LOCK_EX, _m.LOCK_NB, _m.LOCK_UN = 1, 2, 4, 8
    _m.F_GETLK, _m.F_SETLK, _m.F_SETLKW = 5, 6, 7
    _m.F_GETFD, _m.F_SETFD, _m.F_GETFL, _m.F_SETFL = 1, 2, 3, 4
    _m.flock = _win_flock
    _m.lockf = _win_lockf
    _m.fcntl = _m.ioctl = lambda *_a, **_k: 0
    _m._corvin_win_shim = True
    _sys.modules["fcntl"] = _m
    fcntl = _m  # type: ignore[assignment]
