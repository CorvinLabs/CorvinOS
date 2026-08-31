"""On-disk run state (ADR-0013 Phase 13.2).

Layout under ``<corvin_home>/tenants/<tid>/compute/runs/<run_id>/``:

- ``manifest.json``      — tool_name, strategy, budget, accepted_at; written once.
- ``summary.json``       — rolling: best_iter, best_loss, state, last_iteration_at.
- ``iterations/<N>.json``— append-only per iter; written via atomic-replace.

Atomic writes everywhere: temp-file + ``os.replace``. Mode ``0o600`` on
every file. The directory tree is created with ``0o700`` on first
manifest write — the path-gate hook (Layer 10) is the LLM-side defence,
the filesystem ACL is the OS-side defence.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import string
import threading
from pathlib import Path
from typing import Any, Mapping

from .iteration import IterRecord


_RUN_ID_RE = re.compile(r"^compute_[A-Za-z0-9_-]{22}$")
_RUN_ID_ALPHABET = string.ascii_letters + string.digits + "_-"

# Per-process write lock; protects multi-thread runs sharing the same
# run_id (parallel iterations under Phase 13.7).
_state_locks: dict[str, threading.RLock] = {}
_state_locks_global = threading.Lock()


def _lock_for(run_id: str) -> threading.RLock:
    with _state_locks_global:
        lock = _state_locks.get(run_id)
        if lock is None:
            lock = threading.RLock()
            _state_locks[run_id] = lock
        return lock


def new_run_id() -> str:
    """Return a fresh ``compute_<22-url-safe-chars>`` identifier."""
    return "compute_" + "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(22))


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id shape: {run_id!r}")
    return run_id


def run_dir(corvin_home: Path, tenant_id: str, run_id: str) -> Path:
    """Return the path to a run's on-disk directory (no FS side effects)."""
    return Path(corvin_home) / "tenants" / tenant_id / "compute" / "runs" / run_id


def _atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclasses.dataclass
class RunRecord:
    """Aggregate view of one run for the public API surface."""

    run_id: str
    tenant_id: str
    tool_name: str
    strategy: str
    state: str
    best_loss: float | None
    best_iter: int | None
    total_iterations: int
    total_wall_s: float
    accepted_at: float
    started_at: float | None
    last_iteration_at: float | None
    convergence_reason: str
    error: str | None
    manifest: dict[str, Any]
    summary: dict[str, Any]


class RunStore:
    """Owns the on-disk artefacts for one tenant's compute runs.

    Phase 13.2 — sequential single-writer. Phase 13.7 grows a parallel
    write story but the file format stays identical.
    """

    def __init__(self, corvin_home: Path, tenant_id: str) -> None:
        self.corvin_home = Path(corvin_home)
        self.tenant_id = tenant_id
        self.root = self.corvin_home / "tenants" / tenant_id / "compute" / "runs"

    # -- manifest ----------------------------------------------------------------

    def write_manifest(self, run_id: str, manifest: Mapping[str, Any]) -> None:
        run_id = validate_run_id(run_id)
        with _lock_for(run_id):
            _atomic_write_json(self.root / run_id / "manifest.json", dict(manifest))

    def read_manifest(self, run_id: str) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        with _lock_for(run_id):
            return _read_json(self.root / run_id / "manifest.json")

    # -- summary -----------------------------------------------------------------

    def write_summary(self, run_id: str, summary: Mapping[str, Any]) -> None:
        run_id = validate_run_id(run_id)
        with _lock_for(run_id):
            _atomic_write_json(self.root / run_id / "summary.json", dict(summary))

    def read_summary(self, run_id: str) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        with _lock_for(run_id):
            return _read_json(self.root / run_id / "summary.json")

    def summary_mtime(self, run_id: str) -> float | None:
        """Last-write mtime of summary.json — a staleness signal for the
        orphan reaper (last time the run made progress). None if absent."""
        run_id = validate_run_id(run_id)
        try:
            return (self.root / run_id / "summary.json").stat().st_mtime
        except (OSError, FileNotFoundError):
            return None

    # -- iterations --------------------------------------------------------------

    def append_iteration(self, run_id: str, record: IterRecord) -> None:
        run_id = validate_run_id(run_id)
        iter_path = (self.root / run_id / "iterations" /
                     f"{record.iter:04d}.json")
        with _lock_for(run_id):
            _atomic_write_json(iter_path, record.to_json())

    def read_iterations(self, run_id: str) -> list[IterRecord]:
        run_id = validate_run_id(run_id)
        iters_dir = self.root / run_id / "iterations"
        if not iters_dir.is_dir():
            return []
        records: list[IterRecord] = []
        with _lock_for(run_id):
            for f in sorted(iters_dir.glob("*.json")):
                try:
                    records.append(IterRecord.from_json(_read_json(f)))
                except (OSError, json.JSONDecodeError, KeyError, ValueError):
                    continue
        records.sort(key=lambda r: r.iter)
        return records

    # -- listing / lookup --------------------------------------------------------

    def exists(self, run_id: str) -> bool:
        try:
            validate_run_id(run_id)
        except ValueError:
            return False
        return (self.root / run_id / "manifest.json").is_file()

    def list_runs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and _RUN_ID_RE.match(p.name)
        )
