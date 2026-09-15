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

The lock shim degrades ``flock``/``lockf`` to no-ops: forge registries are
single-host and the writes are already atomic via temp-file + rename, so within
one Windows process correctness is unaffected (cross-*process* advisory locks are
a Linux multi-process-deployment concern). ``resource`` rlimit calls become
no-ops; the sandbox rlimit path is POSIX/bwrap-only anyway.
"""
from __future__ import annotations

import sys
import types


def install() -> None:
    """Idempotently register POSIX-only stdlib stand-ins on Windows. No-op on POSIX."""
    if not sys.platform.startswith("win"):
        return

    if "fcntl" not in sys.modules:
        m = types.ModuleType("fcntl")
        # Flag constants (values mirror Linux so any bitwise-OR behaves the same;
        # they are inert because the lock functions are no-ops).
        m.LOCK_SH, m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 1, 2, 4, 8  # type: ignore[attr-defined]
        m.F_GETLK, m.F_SETLK, m.F_SETLKW = 5, 6, 7               # type: ignore[attr-defined]
        m.F_GETFD, m.F_SETFD, m.F_GETFL, m.F_SETFL = 1, 2, 3, 4  # type: ignore[attr-defined]

        def _noop(*_a, **_k):  # flock / lockf / fcntl / ioctl
            return 0

        m.flock = _noop      # type: ignore[attr-defined]
        m.lockf = _noop      # type: ignore[attr-defined]
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
