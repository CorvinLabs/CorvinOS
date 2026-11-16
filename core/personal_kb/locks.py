"""A small cross-process exclusive file lock (POSIX ``fcntl``, Windows ``msvcrt``).

Used for the per-owner write lock and the per-tenant index lock (ADR-2325 item 4). The lock file
is created only inside a directory that already exists; this module never creates directories.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator

LOCK_TIMEOUT_S = 10.0


class LockTimeout(RuntimeError):
    """The lock stayed held past the timeout."""


@contextlib.contextmanager
def exclusive(path: Path, *, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    if not path.parent.is_dir():
        raise FileNotFoundError("lock directory does not exist")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise LockTimeout("lock busy")
                    time.sleep(0.02)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise LockTimeout("lock busy")
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Temp file in the same directory, fsync, os.replace (retried with backoff on Windows)."""
    import tempfile

    d = path.parent
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(d))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        delay = 0.02
        for attempt in range(8):
            try:
                os.replace(tmp, str(path))
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(delay)
                delay *= 2
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
