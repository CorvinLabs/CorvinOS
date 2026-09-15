"""Data-handle store — persistent per-handle metadata for registered
large datasets.

Storage layout (per forge workspace root):

    <root>/
    └── data/
        ├── handles/
        │   ├── data_abc123.json     # one file per handle
        │   └── data_def456.json
        └── snapshots/               # optional cache (Phase 12.5+)

Handle format: ``data_<22 url-safe base64 chars>``. Generated with
``secrets.token_urlsafe(16)`` → 22-char output.

Per-handle JSON shape:

    {
      "handle":          "data_...",
      "path":            "/abs/path/to/file.csv",
      "format":          "csv",
      "size_b":          12345,
      "file_hash":       "sha256:abc...",  // first 16 hex chars
      "registered_at":   1234567890.0,
      "last_snapshot_at": 1234567900.0,
      "tenant_id":       "_default",
      "registered_by":   "<persona>",
    }

The store is the source of truth for "does this handle exist". The
snapshot itself is regenerated on each ``data_snapshot`` call (cheap
for CSV/JSONL, bounded for JSON).

Lock semantics: per-write fcntl.LOCK_EX on a sidecar ``.lock`` file.
Cross-process safe (forge MCP server + bridge adapter both may
write). Atomic-replace pattern for the JSON files themselves.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


# Handle shape: "data_" + 22 url-safe chars.
HANDLE_PREFIX = "data_"
HANDLE_LEN = len(HANDLE_PREFIX) + 22


class HandleStoreError(RuntimeError):
    """Raised on filesystem errors / corrupt store contents."""


class HandleNotFound(KeyError):
    """Raised when a requested handle is not in the store."""


@dataclass
class DataHandle:
    """Per-handle metadata. Mirrors the on-disk JSON shape 1:1."""

    handle:            str
    path:              str
    format:            str
    size_b:            int
    file_hash:         str
    registered_at:     float
    last_snapshot_at:  float = 0.0
    tenant_id:         str = "_default"
    registered_by:     str = ""
    notes:             str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_handle() -> str:
    """Mint a new data handle. 22 url-safe chars after the prefix."""
    return f"{HANDLE_PREFIX}{secrets.token_urlsafe(16)}"


def is_handle_shape(s: str) -> bool:
    """Quick syntactic check — does *s* look like a handle?"""
    if not isinstance(s, str):
        return False
    if not s.startswith(HANDLE_PREFIX):
        return False
    if len(s) != HANDLE_LEN:
        return False
    # url-safe base64 chars: A-Z, a-z, 0-9, -, _
    tail = s[len(HANDLE_PREFIX):]
    return all(c.isalnum() or c in "-_" for c in tail)


def compute_file_hash(path: Path, *, max_read_bytes: int = 64 * 1024) -> str:
    """Cheap content fingerprint: sha256 of (size + first N bytes +
    last N bytes). For huge files we don't want to read the whole
    thing on every register call.

    Returns ``sha256:<16 hex chars>`` — 64 bits of identity, plenty
    for "did this file change since last register".
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode("ascii"))
    h.update(b"\0")
    with path.open("rb") as fh:
        head = fh.read(max_read_bytes)
        h.update(head)
        if size > 2 * max_read_bytes:
            fh.seek(-max_read_bytes, 2)
            tail = fh.read(max_read_bytes)
            h.update(tail)
    return f"sha256:{h.hexdigest()[:16]}"


class DataRegistry:
    """File-backed registry of dataset handles, scoped to one
    workspace root.

    The registry never reads / writes the dataset file itself —
    that's the snapshot pipeline's job. The registry only persists
    handle → metadata mappings.
    """

    HANDLES_DIR = "data/handles"
    LOCK_NAME = ".registry.lock"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.handles_dir = self.root / self.HANDLES_DIR
        self.handles_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.handles_dir / self.LOCK_NAME

    # -- locking + atomic IO ---------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        directory = path.parent
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(directory))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    # -- CRUD ------------------------------------------------------------------

    def _handle_path(self, handle: str) -> Path:
        if not is_handle_shape(handle):
            raise HandleStoreError(f"malformed handle: {handle!r}")
        return self.handles_dir / f"{handle}.json"

    def register(
        self,
        *,
        path:           str | Path,
        fmt:            str,
        registered_by:  str = "",
        tenant_id:      str = "_default",
        notes:          str = "",
    ) -> DataHandle:
        """Register a new dataset; returns the freshly minted handle.

        Computes content hash + size; both land in the handle record.
        Does NOT load or snapshot the file — that's the caller's job
        (typically the data_register MCP tool, which generates a
        snapshot immediately after registering).
        """
        p = Path(path).resolve()
        if not p.exists():
            raise HandleStoreError(f"path does not exist: {path}")
        if not p.is_file():
            raise HandleStoreError(f"path is not a regular file: {path}")

        size_b = p.stat().st_size
        file_hash = compute_file_hash(p)
        h = new_handle()
        rec = DataHandle(
            handle=h,
            path=str(p),
            format=fmt,
            size_b=size_b,
            file_hash=file_hash,
            registered_at=time.time(),
            tenant_id=tenant_id,
            registered_by=registered_by,
            notes=notes,
        )

        with self._locked():
            target = self._handle_path(h)
            self._atomic_write_text(target, json.dumps(rec.to_dict(), indent=2))
            os.chmod(target, 0o600)
        return rec

    def get(self, handle: str) -> DataHandle:
        """Look up a handle by name. Raises HandleNotFound on miss."""
        path = self._handle_path(handle)
        if not path.exists():
            raise HandleNotFound(handle)
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise HandleStoreError(
                f"handle store entry {handle!r} is corrupt: {exc}"
            ) from exc
        return DataHandle(**raw)

    def update_last_snapshot(self, handle: str) -> None:
        """Bump ``last_snapshot_at`` to current wall clock."""
        with self._locked():
            try:
                rec = self.get(handle)
            except HandleNotFound:
                return
            rec.last_snapshot_at = time.time()
            self._atomic_write_text(
                self._handle_path(handle),
                json.dumps(rec.to_dict(), indent=2),
            )

    def delete(self, handle: str) -> bool:
        """Remove a handle. Returns True if a file was removed."""
        with self._locked():
            path = self._handle_path(handle)
            if not path.exists():
                return False
            path.unlink()
            return True

    def list(self) -> list[DataHandle]:
        """List every active handle in this registry."""
        out: list[DataHandle] = []
        for f in self.handles_dir.iterdir():
            if not f.is_file() or not f.name.endswith(".json"):
                continue
            try:
                raw = json.loads(f.read_text())
                out.append(DataHandle(**raw))
            except (json.JSONDecodeError, TypeError):
                continue
        return sorted(out, key=lambda r: r.registered_at)
