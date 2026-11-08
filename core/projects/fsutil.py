"""Filesystem helpers shared by the projects modules: atomic writes that never follow a symlink."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


class UnsafePath(ValueError):
    """A write target that is, or sits below, a symlink, or that leaves its root."""


def ensure_inside(root: Path, target: Path) -> Path:
    """*target* must lie below *root* and no existing component between them may be a symlink."""
    root_abs = Path(os.path.abspath(root))
    tgt_abs = Path(os.path.abspath(target))
    try:
        rel = tgt_abs.relative_to(root_abs)
    except ValueError:
        raise UnsafePath(f"{target} is outside {root}") from None
    cur = root_abs
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            raise UnsafePath(f"{cur} is a symlink")
    return tgt_abs


def atomic_write(path: Path, data: bytes, *, mode: int = 0o644, root: Path | None = None) -> None:
    """Write *data* to *path* through a fresh temp file in the same directory and ``os.replace``.
    With *root* every existing component below it must not be a symlink."""
    path = Path(path)
    if root is not None:
        ensure_inside(root, path)
    elif path.is_symlink():
        raise UnsafePath(f"{path} is a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with contextlib.suppress(OSError):   # chmod is advisory on Windows
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def file_lock(lock_path: Path):
    """Blocking exclusive lock on *lock_path* (flock on POSIX, a one-byte region lock on Windows),
    never following a symlink."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt  # noqa: PLC0415

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
