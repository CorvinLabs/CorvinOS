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


class LayerPrimitive:
    """Atomic read/write of one layer's state, keyed by layer_id.

    write_state: temp-file + os.replace (atomic rename on POSIX) under the lock.
    read_state: plain read under the same lock (consistent-read guarantee).
    """

    def __init__(self, layer_id: str, state_dir: Path):
        self.layer_id = layer_id
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.state_dir / f".{layer_id}.lock"
        self._state_path = self.state_dir / f"{layer_id}.json"

    def _lock(self, timeout_s: float = 30.0) -> _FileLock:
        return _FileLock(self._lock_path, timeout_s=timeout_s)

    def write_state(self, state: dict, *, timeout_s: float = 30.0) -> None:
        with self._lock(timeout_s=timeout_s):
            tmp = self.state_dir / f".{self.layer_id}.{os.getpid()}.{time.monotonic_ns()}.tmp"
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
            os.replace(tmp, self._state_path)

    def read_state(self, *, timeout_s: float = 30.0) -> dict:
        with self._lock(timeout_s=timeout_s):
            if not self._state_path.exists():
                return {}
            return json.loads(self._state_path.read_text())
