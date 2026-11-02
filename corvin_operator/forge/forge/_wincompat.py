"""Windows compatibility shim for the ``forge`` package (and its importers).

Many forge submodules — ``registry``, ``runner``, ``security_events``,
``permissions``, ``artifacts``, ``sandbox``, ``corvin_data`` — do a module-level
``import fcntl`` (advisory file locking) and ``sandbox`` also ``import resource``
(rlimits). Those stdlib modules do not exist on Windows, so a bare top-level
import crashed forge AT IMPORT TIME. Because ``from forge import paths`` is an
unguarded top-level import in the gateway, license, compliance and console
packages, that single crash took the whole server down on Windows — and where a
caller wrapped the forge import in ``try/except`` it instead SILENTLY disabled
the L16 hash-chained audit log (GDPR Art. 30/32, load-bearing).

This installs no-op stand-ins into ``sys.modules`` *before* any forge submodule
runs its ``import fcntl`` / ``import resource``. It is called as the very first
statement of ``forge/__init__`` so it wins the race. On POSIX it is a complete
no-op — the real stdlib modules are used unchanged.

The lock shim implements ``flock``/``lockf`` with real ``LockFileEx`` locks
(see ``_win_flock``): forge registries are read-modify-written by the console,
the forge MCP server and the bridge adapter — separate processes — so a no-op
lock lost updates on Windows. ``fcntl()``/``ioctl()`` stay no-ops. ``resource``
rlimit calls become no-ops; the sandbox rlimit path is POSIX/bwrap-only anyway.
"""
from __future__ import annotations

import errno as _errno
import sys
import types

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



def install() -> None:
    """Idempotently register POSIX-only stdlib stand-ins on Windows. No-op on POSIX."""
    if not sys.platform.startswith("win"):
        return

    if "fcntl" not in sys.modules:
        m = types.ModuleType("fcntl")
        # Flag constants (values mirror Linux so any bitwise-OR behaves the same;
        # flock/lockf honour them, fcntl/ioctl are no-ops).
        m.LOCK_SH, m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 1, 2, 4, 8  # type: ignore[attr-defined]
        m.F_GETLK, m.F_SETLK, m.F_SETLKW = 5, 6, 7               # type: ignore[attr-defined]
        m.F_GETFD, m.F_SETFD, m.F_GETFL, m.F_SETFL = 1, 2, 3, 4  # type: ignore[attr-defined]

        def _noop(*_a, **_k):  # fcntl / ioctl
            return 0

        m.flock = _win_flock  # type: ignore[attr-defined]
        m.lockf = _win_lockf  # type: ignore[attr-defined]
        m.fcntl = _noop      # type: ignore[attr-defined]
        m.ioctl = _noop      # type: ignore[attr-defined]
        sys.modules["fcntl"] = m

    if "resource" not in sys.modules:
        r = types.ModuleType("resource")
        r.RLIMIT_AS = 9          # type: ignore[attr-defined]
        r.RLIMIT_CPU = 0         # type: ignore[attr-defined]
        r.RLIMIT_NOFILE = 7      # type: ignore[attr-defined]
        r.RLIMIT_FSIZE = 1       # type: ignore[attr-defined]
        r.RLIM_INFINITY = -1     # type: ignore[attr-defined]

        def _getrlimit(_res):    # returns (soft, hard)
            return (-1, -1)

        def _setrlimit(*_a, **_k):
            return None

        r.getrlimit = _getrlimit  # type: ignore[attr-defined]
        r.setrlimit = _setrlimit  # type: ignore[attr-defined]
        sys.modules["resource"] = r

    install_replace_retry()
    install_binary_os_open()


# Windows: os.replace() onto a file another process (or thread) currently has
# open fails with PermissionError (WinError 5 / 32) because Python opens files
# without FILE_SHARE_DELETE. Every temp-file + os.replace "atomic write" in the
# tree therefore failed sporadically whenever the console, adapter or a hook
# happened to be reading the target at that moment. The readers hold the file
# only for a moment, so a short bounded retry turns that race into a wait.
# Genuine denials (read-only file, ACL) still raise after the deadline.
_REPLACE_RETRY_MARK = "_corvin_replace_retry"
_REPLACE_RETRY_SECONDS = 1.0


def install_replace_retry() -> None:
    """Make ``os.replace`` (and ``Path.replace``) retry transient sharing
    violations on Windows. Idempotent; no-op on POSIX."""
    if not sys.platform.startswith("win"):
        return
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415

    original = os.replace
    if getattr(original, _REPLACE_RETRY_MARK, False):
        return

    def replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
        delay = 0.005
        while True:
            try:
                return original(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or time.monotonic() >= deadline:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)

    setattr(replace, _REPLACE_RETRY_MARK, True)
    replace.__doc__ = original.__doc__
    os.replace = replace


# Windows: os.open() without O_BINARY yields a CRT *text-mode* descriptor, so a
# raw os.write(fd, b"...\n") lands on disk as "\r\n" and os.read() drops "\r"
# and stops at 0x1A. Dozens of call sites append JSONL / hash-chained or signed
# records through os.open + os.write; on Windows their bytes silently differed
# from what was hashed. Default every os.open() to binary unless the caller
# asked for O_TEXT explicitly â€” byte-identical to POSIX, where no translation
# exists. builtin open()/io already passes O_BINARY, so it is unaffected.
_BINARY_OPEN_MARK = "_corvin_binary_os_open"


def install_binary_os_open() -> None:
    """Make ``os.open`` default to ``O_BINARY`` on Windows. Idempotent; no-op on POSIX."""
    if not sys.platform.startswith("win"):
        return
    import os  # noqa: PLC0415

    original = os.open
    if getattr(original, _BINARY_OPEN_MARK, False):
        return
    text_or_binary = os.O_TEXT | os.O_BINARY

    def open(path, flags, mode=0o777, *, dir_fd=None):  # noqa: A001 - mirrors os.open
        if not flags & text_or_binary:
            flags |= os.O_BINARY
        return original(path, flags, mode, dir_fd=dir_fd)

    setattr(open, _BINARY_OPEN_MARK, True)
    open.__doc__ = original.__doc__
    os.open = open
