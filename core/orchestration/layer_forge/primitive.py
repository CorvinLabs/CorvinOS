"""LayerPrimitive — the single place race-conditions on layer-state are
prevented (ADR-2222 D4). Every writer goes through this; no caller implements
its own locking (recurring-race-fix memory: fix the primitive, not the call site).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class LockTimeoutError(RuntimeError):
    pass


class _FileLock:
    """Minimal stdlib-only exclusive file lock (no third-party `filelock` dep).

    Uses O_CREAT|O_EXCL on a sibling `.lock` file as the mutual-exclusion
    primitive — atomic on POSIX filesystems. Stale locks older than
    `stale_after_s` are reclaimed (crash recovery), guarded by re-checking
    the lock file's mtime right before unlinking it (best-effort, not
    distributed-safe, matches the MVP's single-host scope).
    """

    def __init__(self, path: Path, timeout_s: float = 30.0, stale_after_s: float = 300.0):
        self.path = path
        self.timeout_s = timeout_s
        self.stale_after_s = stale_after_s
        self._fd: int | None = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_after_s:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"could not acquire lock: {self.path}")
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)
        return False


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON so a reader sees the old or the new file, never a torn one."""
    path = Path(path)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class LayerPrimitive:
    """Mutual exclusion for one registry entry id.

    Every read-modify-write of an entry's state (create, status transition, and
    the audit record that precedes it) runs inside ``locked()``; files are
    replaced with ``atomic_write_json``. The lock is not re-entrant.
    """

    def __init__(self, layer_id: str, lock_dir: Path):
        self.layer_id = layer_id
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.lock_dir / f".{layer_id}.lock"

    def locked(self, timeout_s: float = 30.0) -> _FileLock:
        return _FileLock(self._lock_path, timeout_s=timeout_s)
