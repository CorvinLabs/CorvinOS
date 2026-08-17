"""Windows compatibility shim.

CorvinOS (and the vendored ``operator/`` subtrees) do a module-level
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


install()
