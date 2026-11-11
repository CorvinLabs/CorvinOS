"""Filesystem helpers shared by the projects modules: atomic writes that never follow a link, and the ONE
answer to "is this a link?".

On Windows ``Path.is_symlink()`` is False for a directory junction (``mklink /J``, no admin right needed)
and for other name-surrogate reparse points (mount points, WCI). Every check in the projects modules that
means "never follow this" goes through :func:`is_link`, which also says yes to those. A reparse point that is
NOT a name surrogate (OneDrive/Cloud Files placeholders, dedup, ...) is an ordinary file or folder and stays one.
"""
from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Optional

#: a reparse tag with this bit set names another place (symlink, junction/mount point, WCI ...)
_NAME_SURROGATE = 0x20000000
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_IO_REPARSE_TAG_SYMLINK = 0xA000000C


class UnsafePath(ValueError):
    """A write target that is, or sits below, a link, or that leaves its root."""


class LockBusy(OSError):
    """Another request holds the lock longer than this one may wait."""


class WriteFailed(OSError):
    """A write the filesystem refused (read-only file, a lock held by another program, no permission).
    The message names the reason class, never the path."""


def dirent_is_junction(e: "os.DirEntry[str]") -> bool:
    """A directory entry is a junction / reparse point. ``DirEntry.is_junction()`` exists only from Python 3.12; on 3.11 (the minimum this
    project supports) the same question is answered by ``is_link`` on the entry's path (review T-0406: the call raised AttributeError
    and broke project creation on the live 3.11 console)."""
    native = getattr(e, "is_junction", None)
    if native is not None:
        return bool(native())
    return is_link(e.path) and not e.is_symlink()


def is_link(p: Path | str) -> bool:
    """True for a symlink, a junction or any other name-surrogate reparse point. Never follows the link;
    a path that does not exist (or cannot be stat-ed) is not a link."""
    try:
        st = os.lstat(p)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    tag = getattr(st, "st_reparse_tag", 0) or 0
    if tag:
        return tag in (_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK) or bool(tag & _NAME_SURROGATE)
    return False


def passes_link(root: Path, target: Path) -> bool:
    """True when *target* itself or any component between *root* (exclusive) and it is a link, or when it
    does not lie below *root* at all. Lexical (``abspath``), so a link is seen before anything follows it."""
    root_abs = Path(os.path.abspath(root))
    tgt_abs = Path(os.path.abspath(target))
    try:
        rel = tgt_abs.relative_to(root_abs)
    except ValueError:
        return True
    cur = root_abs
    for part in rel.parts:
        cur = cur / part
        if is_link(cur):
            return True
    return False


def ensure_inside(root: Path, target: Path) -> Path:
    """*target* must lie below *root* and no existing component between them may be a link."""
    root_abs = Path(os.path.abspath(root))
    tgt_abs = Path(os.path.abspath(target))
    try:
        rel = tgt_abs.relative_to(root_abs)
    except ValueError:
        raise UnsafePath(f"{target} is outside {root}") from None
    cur = root_abs
    for part in rel.parts:
        cur = cur / part
        if is_link(cur):
            raise UnsafePath(f"{cur} is a link")
    return tgt_abs


#: no project file the console reads whole may be larger than this (a multi-gigabyte "CLAUDE.md" must not exhaust the console)
MAX_READ_BYTES = 1024 * 1024


def read_bytes_limited(p: Path, limit: int = MAX_READ_BYTES) -> bytes:
    """The bytes of *p*, or ValueError when it is larger than *limit* (checked by stat BEFORE anything is read)."""
    if os.stat(p).st_size > limit:
        raise ValueError("file is too large")
    return Path(p).read_bytes()


def read_text_any(p: Path, *, limit: int | None = None) -> str:
    """Read a text file the way Windows tools write it: UTF-8 with or without BOM, or UTF-16 with a BOM
    (PowerShell 5 ``Out-File``/``>``). Raises ``UnicodeError`` for anything else - never guesses a codepage."""
    if limit is not None and os.stat(p).st_size > limit:
        raise ValueError("file is too large")
    data = Path(p).read_bytes()
    if limit is not None and len(data) > limit:
        raise ValueError("file is too large")
    text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig")
    if "\x00" in text:
        # UTF-16 without a BOM (or a binary file) decodes as "valid" UTF-8 full of NULs: never text a caller should rebuild or parse
        raise UnicodeError("the file holds NUL characters (UTF-16 without a byte order mark, or binary)")
    return text


def _replace(tmp: str, path: Path, *, owned: bool) -> None:
    import time  # noqa: PLC0415
    # Windows: os.replace fails with PermissionError while ANY process (this one included - a parallel request reading the registry)
    # has the target open without FILE_SHARE_DELETE. Such a read lasts milliseconds: retry with a short bounded backoff first.
    for delay in (0.01, 0.02, 0.03, 0.05, 0.05, 0.1, 0.1, 0.2, 0.2, 0.25) if os.name == "nt" else ():
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if path.exists() and not os.access(path, os.W_OK):
                break                                          # read-only: waiting does not help
            time.sleep(delay)
    try:
        os.replace(tmp, path)
    except PermissionError:
        # Windows refuses to replace a READ-ONLY file. A file this module owns (the registry) gets its write bit
        # back and one more try; a file the user owns (project.yaml) is never made writable behind their back.
        if owned and os.name == "nt" and path.exists() and not os.access(path, os.W_OK):
            with contextlib.suppress(OSError):
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            try:
                os.replace(tmp, path)
                return
            except OSError:
                pass
        if path.exists() and not os.access(path, os.W_OK):
            raise WriteFailed(f"{path.name} is read-only; clear its read-only attribute and try again") from None
        raise WriteFailed(f"{path.name} could not be written (it is locked by another program or access is denied)") from None


def atomic_write(path: Path, data: bytes, *, mode: int = 0o644, root: Path | None = None, owned: bool = False) -> None:
    """Write *data* to *path* through a fresh temp file in the same directory and ``os.replace``.
    With *root* every existing component below it must not be a link. ``owned``: the file belongs to the
    platform (not the user), so a read-only bit on it may be cleared to replace it."""
    path = Path(path)
    if root is not None:
        ensure_inside(root, path)
    elif is_link(path):
        raise UnsafePath(f"{path} is a link")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    except PermissionError:
        raise WriteFailed(f"{path.name} could not be written (access to its folder is denied)") from None
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        with contextlib.suppress(OSError):   # chmod is advisory on Windows
            os.chmod(tmp, mode)
        _replace(tmp, path, owned=owned)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 60.0):
    """Exclusive lock on *lock_path* (flock on POSIX, a one-byte region lock on Windows), never following a symlink. Waits up to
    *timeout* seconds, then raises :class:`LockBusy` (Windows' own LK_LOCK gave up after 10 s with a PermissionError that read like a
    filesystem refusal - adversarial review round 4)."""
    import time  # noqa: PLC0415
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockBusy("another request is still working on this; try again in a moment") from None
                time.sleep(0.05)
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _fd_dirs() -> bool:
    """mkdir/open/rmdir relative to a directory descriptor: POSIX (``os.fwalk`` exists exactly where open/stat take
    ``dir_fd``); never on Windows."""
    return os.name != "nt" and bool(_O_NOFOLLOW) and bool(_O_DIRECTORY) and hasattr(os, "fwalk")


def _ident(st: os.stat_result) -> tuple[int, int]:
    return st.st_dev, st.st_ino


class DirHandle:
    """A directory made or found by :func:`make_dirs_nofollow`, held open: ``fd`` (POSIX; None on Windows), ``ident``
    (``st_dev``, ``st_ino``) and ``created`` (the directories this call made, outermost first). :meth:`rollback` removes
    exactly those (only while empty), :meth:`close` releases the descriptors."""

    def __init__(self, path: Path):
        self.path = path
        self.fd: Optional[int] = None
        self.ident: tuple[int, int] = (0, 0)
        self.created: list[tuple[Optional[int], str, Path]] = []   # (parent fd or None, name, full path)
        self._fds: list[int] = []

    def still_here(self) -> bool:
        """The path still names the directory this handle holds, and is not a link."""
        try:
            st = os.lstat(self.path)
        except OSError:
            return False
        return not is_link(self.path) and stat.S_ISDIR(st.st_mode) and _ident(st) == self.ident

    def rollback(self) -> None:
        for pfd, name, full in reversed(self.created):
            with contextlib.suppress(OSError):
                if pfd is not None:
                    os.rmdir(name, dir_fd=pfd)
                elif not is_link(full):
                    os.rmdir(full)
        self.created = []

    def close(self) -> None:
        """Release the descriptors; ``created`` keeps the paths (a later :meth:`rollback` then goes by path)."""
        self.created = [(None, n, f) for _p, n, f in self.created]
        for x in [*self._fds, *([self.fd] if self.fd is not None else [])]:
            with contextlib.suppress(OSError):
                os.close(x)
        self._fds = []
        self.fd = None


def make_dirs_nofollow(target: Path, *, mode: int = 0o777) -> DirHandle:
    """Create *target* - an absolute path whose existing part was judged in its RESOLVED form (no link left in it) - and its
    missing parents without following a link at any component (REVIEW-0063 m4: ``mkdir(parents=True)`` follows a component
    swapped for a link after the verdict).

    POSIX: every component from the anchor is opened ``O_DIRECTORY|O_NOFOLLOW`` relative to its parent's descriptor; a missing
    one is made with ``mkdir(name, dir_fd=parent)`` and opened the same way. A component that is a link (or not a directory)
    raises :class:`UnsafePath` and the directories made so far are removed. Windows (no ``dir_fd``): each component is checked
    with :func:`is_link` before and its identity after ``mkdir``; a swap undone between two checks is not seen.

    The caller holds the returned :class:`DirHandle` while it verifies the result and closes it."""
    target = Path(os.path.abspath(target))
    h = DirHandle(target)
    parts = target.parts
    try:
        if _fd_dirs():
            cur = os.open(parts[0], os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
            try:
                for i, name in enumerate(parts[1:], start=1):
                    try:
                        nxt = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=cur)
                    except FileNotFoundError:
                        try:
                            os.mkdir(name, mode, dir_fd=cur)
                        except FileExistsError:
                            pass                                          # made in between: opened (and judged) below
                        else:
                            pfd = os.dup(cur)
                            h._fds.append(pfd)
                            h.created.append((pfd, name, Path(*parts[:i + 1])))
                        try:
                            nxt = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=cur)
                        except OSError:
                            raise UnsafePath(f"{Path(*parts[:i + 1])} is a link or not a folder") from None
                    except OSError:
                        raise UnsafePath(f"{Path(*parts[:i + 1])} is a link or not a folder") from None
                    os.close(cur)
                    cur = nxt
            except BaseException:
                os.close(cur)
                raise
            h.fd = cur
            h.ident = _ident(os.fstat(cur))
        else:
            cur_p = Path(parts[0])
            for i, name in enumerate(parts[1:], start=1):
                cur_p = cur_p / name
                if is_link(cur_p):
                    raise UnsafePath(f"{cur_p} is a link")
                if not cur_p.exists():
                    try:
                        os.mkdir(cur_p, mode)
                        h.created.append((None, name, cur_p))
                    except FileExistsError:
                        pass
                if is_link(cur_p) or not cur_p.is_dir():
                    raise UnsafePath(f"{cur_p} is a link or not a folder")
            h.ident = _ident(os.lstat(target))
    except BaseException:
        h.rollback()
        h.close()
        raise
    return h
