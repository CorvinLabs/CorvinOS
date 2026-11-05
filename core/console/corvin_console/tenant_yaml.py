"""The ONE writer for ``tenant.corvin.yaml`` in the console (review SRF-3/SRF-4).

``tenant.corvin.yaml`` is co-owned (CLAUDE.md "Worker-Engine Model Routing"): the
console writes several ``spec`` sections and the gateway reads the same file
FAIL-CLOSED on its mode - ``corvin_gateway.tenant_config.load()`` refuses a file
wider than 0o600, which turns every gateway run terminal-``failed`` before an
engine spawns. Each route used to carry its own writer and two of them wrote at
umask (``os.replace`` carries the temp file's mode onto the target), one of them
non-atomically; another read a parse failure as ``{}`` and wrote back a file that
held only its own section, dropping engine pins, data residency, budget and the
telemetry opt-outs.

This module owns the three rules every writer must keep:

* **mode** - the temp file is ``chmod 0o600`` BEFORE ``os.replace``, so the live
  path is never wider than 0o600, not even for a moment;
* **atomicity** - temp file in the same directory + ``os.replace``;
* **no wipe** - a file that exists but cannot be read or parsed as a mapping is
  refused (:class:`TenantYamlUnreadable`), never treated as an empty config; the
  read-modify-write runs under one lock (:func:`update`) so two writers that both
  use it cannot interleave.

A read-modify-write MUST be :func:`update` (the lock is held from the read to
the replace); a bare :func:`load_for_write` followed by :func:`write_mapping`
lets a concurrent locked update land in between and be silently undone (review
RC-3). The gateway's writer (``corvin_gateway.tenant_config.save``) imports this
module, so the console and the CLI take the same ``flock``.

**Symlinks** - a ``tenant.corvin.yaml`` that is a symlink (a shared config) is
written THROUGH to its target; the link itself is never replaced by a file.

**Comments are not preserved** by :func:`write_mapping` / :func:`update`: the
document is re-dumped with ``yaml.safe_dump``, so it starts with
:data:`MANAGED_HEADER` saying so. Only the raw editor (:func:`write_text`, used
by the Settings page's YAML editor) keeps the text, comments included.

Callers keep their own path resolver (tests patch those); every write of this
file goes through :func:`write_mapping` / :func:`write_text` / :func:`update`.
``core/console/tests/test_tenant_yaml_writer.py`` fails on a new console or
gateway writer of this file that bypasses it.
"""
from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import yaml

T = TypeVar("T")

FILENAME = "tenant.corvin.yaml"
MODE = 0o600
MANAGED_HEADER = ("# managed by console - rewritten by the CorvinOS console and CLI; "
                  "comments in this file are not preserved\n")

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


class TenantYamlUnreadable(RuntimeError):
    """The file exists but is not a readable YAML mapping - refuse the mutation."""


def target_path(path: Path) -> Path:
    """The file a write of *path* lands in: a symlink is followed to its final
    target (written through, never replaced); anything else is *path* itself."""
    return Path(os.path.realpath(path)) if os.path.islink(path) else path


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()) if path.exists() else str(path.absolute())
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Serialise read-modify-write of *path*: an in-process lock plus, on POSIX,
    an advisory ``flock`` on a sidecar file so a second console process (gateway +
    standalone on one home) cannot interleave either."""
    path = target_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path):
        try:
            import fcntl  # noqa: PLC0415 - POSIX only
        except ImportError:  # Windows: one console process per home; the thread lock covers it
            yield
            return
        lock_path = path.with_name("." + path.name + ".lock")
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def load_for_write(path: Path) -> dict[str, Any]:
    """The current document for a writer. Missing or empty -> ``{}``. Present but
    unreadable, unparseable or not a mapping -> :class:`TenantYamlUnreadable`."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TenantYamlUnreadable(f"{FILENAME} is unreadable ({type(exc).__name__})") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TenantYamlUnreadable(f"{FILENAME} is not valid YAML") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TenantYamlUnreadable(f"{FILENAME} is not a mapping")
    return data


def write_text(path: Path, text: str) -> None:
    """Atomically replace *path* with *text* at mode 0o600 (chmod before replace).
    A symlinked *path* is written through to its target."""
    path = target_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tenant.corvin.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, MODE)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_mapping(path: Path, data: dict[str, Any]) -> None:
    """Dump *data* as YAML and write it through :func:`write_text`."""
    write_text(path, MANAGED_HEADER + yaml.safe_dump(data, default_flow_style=False,
                                                     sort_keys=False, allow_unicode=True))


def update(path: Path, mutate: Callable[[dict[str, Any]], T]) -> T:
    """Locked read-modify-write. *mutate* edits the loaded document in place and
    returns a value passed back to the caller. Raises :class:`TenantYamlUnreadable`
    (nothing written) when the current file cannot be read as a mapping."""
    with locked(path):
        data = load_for_write(path)
        result = mutate(data)
        write_mapping(path, data)
        return result
