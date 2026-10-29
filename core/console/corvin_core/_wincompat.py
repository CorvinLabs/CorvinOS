"""Windows compatibility shim.

CorvinOS (and the vendored ``corvin_operator/`` subtrees) do a module-level
``import fcntl`` / ``import resource`` in ~30 places for advisory file locking
and rlimit handling. Those stdlib modules do not exist on Windows, so a fresh
``pip install corvinos`` + ``corvin-serve`` would crash at import time on
Windows.

This module installs no-op stand-ins into ``sys.modules`` *before* any of those
imports run, so the console (a single-process FastAPI/uvicorn app) is importable
and runnable on Windows. It is imported as the very first statement of
``corvin_console/__init__`` so it wins the race against every submodule.

On POSIX this is a complete no-op — the real stdlib modules are used.

Caveat: the Windows ``fcntl`` shim makes ``flock``/``lockf`` no-ops. The console
runs as a single process, so the cross-*process* advisory locks these guard are
a Linux multi-process deployment concern; within one process correctness is
unaffected. A real Windows lock (``msvcrt.locking``) can be layered in later if
multi-process Windows hosting is ever supported.
"""
from __future__ import annotations

import sys
import types


def install() -> None:
    """Idempotently register POSIX-only stdlib stand-ins on Windows."""
    if not sys.platform.startswith("win"):
        return

    if "fcntl" not in sys.modules:
        m = types.ModuleType("fcntl")
        # Constants used as flags by callers (values mirror Linux for sanity).
        m.LOCK_SH, m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 1, 2, 4, 8
        m.F_GETLK, m.F_SETLK, m.F_SETLKW = 5, 6, 7
        m.F_GETFD, m.F_SETFD, m.F_GETFL, m.F_SETFL = 1, 2, 3, 4

        def _noop(*_a, **_k):  # flock / lockf / fcntl / ioctl
            return 0

        m.flock = _noop
        m.lockf = _noop
        m.fcntl = _noop
        m.ioctl = _noop
        m._corvin_win_shim = True  # marker so self-tests can detect the shim
        sys.modules["fcntl"] = m

    if "resource" not in sys.modules:
        m = types.ModuleType("resource")
        m.RLIMIT_CPU, m.RLIMIT_DATA, m.RLIMIT_NOFILE, m.RLIMIT_AS = 0, 2, 7, 9
        m.RLIM_INFINITY = -1

        def _getrlimit(*_a, **_k):
            return (-1, -1)

        def _setrlimit(*_a, **_k):
            return None

        m.getrlimit = _getrlimit
        m.setrlimit = _setrlimit
        m._corvin_win_shim = True
        sys.modules["resource"] = m


# Windows: a console-subsystem child (cmd, node, claude.exe, python.exe, git ...)
# started from a parent that has NO console gets a brand-new, VISIBLE console
# window. Corvin's processes are console-less whenever they run hidden (VBS
# launchers, Start-Process -WindowStyle Hidden, detached daemons), so every
# one-shot spawn would flash a terminal. Per-call-site fixes kept missing sites,
# so the default is set here, once, for every spawn. A parent that HAS a console
# (the user's own terminal, or a hidden one) is left alone: its children share
# that console and open no window, and interactive foreground commands keep
# working.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_CONSOLE = 0x00000010
_NO_WINDOW_MARK = "_corvin_no_window_default"


def _parent_has_console() -> bool:
    import ctypes  # noqa: PLC0415 — Windows-only path

    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:  # noqa: BLE001 — cannot tell: assume none, add the flag
        return False


def _no_window_flags(flags: int) -> int:
    if flags & _CREATE_NO_WINDOW:
        return flags
    if flags & (_DETACHED_PROCESS | _CREATE_NEW_CONSOLE):
        # Both make Windows open a visible console for the child later on.
        # CREATE_NO_WINDOW keeps the detachment without showing anything.
        return (flags & ~(_DETACHED_PROCESS | _CREATE_NEW_CONSOLE)) | _CREATE_NO_WINDOW
    if _parent_has_console():
        return flags
    return flags | _CREATE_NO_WINDOW


def install_no_console_window() -> None:
    """Make every ``subprocess.Popen`` on Windows open no visible console window.

    Patches ``Popen.__init__`` so ``subprocess.run``/``call``/``check_output``,
    ``shell=True`` and the asyncio subprocess transport all pass through it.
    Idempotent. A positional ``creationflags`` argument is left untouched.
    """
    if not sys.platform.startswith("win"):
        return
    import subprocess  # noqa: PLC0415

    original_init = subprocess.Popen.__init__
    if getattr(original_init, _NO_WINDOW_MARK, False):
        return
    creationflags_position = 13  # Popen(args, bufsize, executable, stdin, stdout,
    #   stderr, preexec_fn, close_fds, shell, cwd, env, universal_newlines,
    #   startupinfo, creationflags, ...) — positional args[0] is the command

    def __init__(self, *args, **kwargs):
        if len(args) <= creationflags_position:
            kwargs["creationflags"] = _no_window_flags(kwargs.get("creationflags") or 0)
        original_init(self, *args, **kwargs)

    setattr(__init__, _NO_WINDOW_MARK, True)
    subprocess.Popen.__init__ = __init__


install()
install_no_console_window()
