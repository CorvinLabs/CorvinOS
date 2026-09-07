"""Bounded advisory file locking for the shared/ registries.

Every registry in ``operator/bridges/shared/`` serialises its
read-modify-write cycles with a POSIX advisory lock on a ``.lock`` sidecar.
Until 2026-09-07 each of them took that lock as a plain
``fcntl.flock(fd, LOCK_EX)`` with **no timeout**. A wedged holder — a crashed
writer whose fd the kernel had not yet reaped, an NFS mount that stopped
answering, a debugger-stopped process — therefore hung the caller FOREVER, and
no ``try/except`` can catch a hang. Several of these modules sit on operator
request paths (console routes, the bridge message path, the L38 remote-trigger
receiver), so one wedged lock file took the request down with it.

This module is the ONE bounded-acquire helper those registries share. Contract
and constant style are identical to the three in-tree precedents —
``core.infinite_session.event_store``, ``core.infinite_session.rollback_manager``
and ``core.console.corvin_console.routes.workflows`` — namely:

* ``LOCK_EX | LOCK_NB`` in a retry loop with a hard deadline;
* a :class:`LockBusy` exception deriving from ``TimeoutError`` (hence
  ``OSError``) at the deadline;
* every caller turning that into a clean refusal (HTTP 503) or a *documented*
  degrade — never into an implicit "proceed as if the check passed".

The deadline is passed in by the caller, never read from here, so each module
keeps its own ``LOCK_TIMEOUT_SECONDS`` module constant that a test can patch.

Windows: ``_compat_fcntl`` degrades ``flock`` to a no-op, so
:func:`acquire_exclusive` returns immediately there — unchanged from the
blocking version's behaviour.
"""
from __future__ import annotations

import time
from typing import Any

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op on Windows

#: Default deadline. Modules re-export their own copy so tests can patch one
#: module without affecting the others.
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_RETRY_INTERVAL_SECONDS = 0.01


class LockBusy(TimeoutError):
    """An advisory registry lock stayed held past the caller's deadline.

    A ``TimeoutError`` (hence an ``OSError``), matching
    :class:`core.infinite_session.event_store.SnapshotLockBusy`, so call sites
    that already degrade on I/O failure keep degrading instead of raising —
    but ONLY where degrading is documented and safe. A lock that guards
    compliance state (consent, disclosure, roles, quota) must refuse instead:
    a busy lock may never become an implicit allow.
    """


def acquire_exclusive(fd: Any, what: str, *, timeout: float) -> None:
    """Take an exclusive advisory lock on ``fd``, or raise at the deadline.

    Args:
        fd: an open file descriptor (int) or file object, as ``flock`` accepts.
        what: short description used in the exception message (no user content).
        timeout: hard deadline in seconds; the call never waits longer.

    Raises:
        LockBusy: the lock was still held when ``timeout`` elapsed.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockBusy(
                    f"{what} lock busy: still held after {timeout:g}s — "
                    f"refusing to block the caller"
                ) from None
            time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
