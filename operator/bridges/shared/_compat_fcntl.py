"""Portable fcntl shim — real ``fcntl`` on POSIX, a no-op flock fallback on Windows.

The shared/ modules use ONLY the advisory-file-lock family (``flock`` +
``LOCK_EX``/``LOCK_SH``/``LOCK_UN``/``LOCK_NB``) for per-file registry locking.
On Windows ``fcntl`` does not exist; a bare top-level ``import fcntl`` therefore
crashed mandatory layers at IMPORT time — including L19 bot-disclosure
(disclosure.py, EU AI Act Art. 50), the ADR-0156 Custom-Layer registry, consent
(L16 Phase 4), and the L36 erasure orchestrator — so the whole module failed to
load on Windows (security-audit 2026-06-25 #11/#12).

We degrade to a no-op ``flock`` on Windows: these are single-host registries and
a Windows desktop install does not provide POSIX advisory locks; the writes are
still atomic via the existing temp-file + rename. POSIX behaviour is unchanged
(the real module is re-exported). Callers do ``from _compat_fcntl import fcntl``
and keep using ``fcntl.flock(fd, fcntl.LOCK_EX)`` verbatim.
"""
from __future__ import annotations

try:  # Linux / macOS — use the real module unchanged.
    import fcntl as fcntl  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover - exercised only on Windows
    class _NoFcntl:
        # Constant values mirror CPython's POSIX fcntl so any code that ORs them
        # behaves identically; they are inert because flock() is a no-op.
        LOCK_SH = 1
        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8

        @staticmethod
        def flock(fd, operation):  # noqa: D401, ARG004 - advisory lock no-op
            return None

        @staticmethod
        def lockf(fd, operation, *args):  # noqa: ARG004 - advisory lock no-op
            return None

    fcntl = _NoFcntl()  # type: ignore[assignment]
