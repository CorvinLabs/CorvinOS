"""Phase A: Event Store with Snapshot Persistence (ADR-0540, Infinite Session Engine).

The ONE persistence layer of the infinite-session engine. The dashboard API,
the session bridger, the audit verifier and the task-turn producer all read
and write snapshots exclusively through this class — there is no second
"checkpoints" layout.

Storage layout (per tenant, built via :mod:`core.infinite_session.paths`):

    <corvin_home>/tenants/<tenant_id>/infinite_session/snapshots/
        <task_id>/
            index.json              # ordered SnapshotMetadata list (seq asc)
            <snapshot_id>.json      # full Snapshot

Guarantees:
- Tenant-bound: the store is constructed for ONE tenant; every call re-checks
  the tenant it is given against the binding (fail-closed).
- Audit-first: the core hash-chained audit writer commits an
  ``infinite_session.snapshot_created`` record BEFORE the snapshot becomes
  visible; no chain commit → no snapshot (ADR-0232/0233).
- Atomic + durable: temp file → fsync → rename; index updated under an
  exclusive file lock.
- Append-only: snapshots are never rewritten; the chain per task is verified
  with :meth:`verify_snapshot_chain`.
- Content-free audit: records carry ids, hashes and byte sizes only.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from core.infinite_session.paths import (
    AuditFn,
    content_free,
    core_audit,
    safe_child,
    tenant_root,
    validate_id,
)
from core.infinite_session.snapshot_schema import (
    Snapshot,
    SnapshotMetadata,
    SnapshotType,
)
from core.tenants import validate_tenant_id

INDEX_FILE = "index.json"
EVENT_SNAPSHOT_CREATED = "infinite_session.snapshot_created"
EVENT_SNAPSHOT_ARCHIVED = "infinite_session.snapshot_archived"


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(target: Path, payload: dict[str, Any]) -> None:
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    _fsync_dir(target.parent)


class EventStore:
    """Append-only, tenant-bound snapshot store (ADR-0540)."""

    def __init__(
        self,
        tenant_id: str,
        corvin_home: Optional[str | Path] = None,
        audit: Optional[AuditFn] = None,
    ):
        """Bind the store to ``tenant_id``.

        Args:
            tenant_id: The ONLY tenant this store may read or write.
            corvin_home: Optional root override (tests); default honours
                ``CORVIN_HOME`` via :func:`core.paths.tenant.corvin_home`.
            audit: ``audit(event_type, *, tenant_id, details) -> audit_ref``.
                Defaults to the core hash-chained writer. Must raise on failure.
        """
        self.tenant_id = validate_tenant_id(tenant_id)
        self.root_dir = tenant_root(self.tenant_id, corvin_home) / "snapshots"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._audit: AuditFn = audit or core_audit

    # ── tenant binding ───────────────────────────────────────────────────

    def _bind(self, tenant_id: Any) -> str:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required (fail-closed)")
        validate_tenant_id(tenant_id)
        if tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: store is bound to {self.tenant_id!r}, got {tenant_id!r}"
            )
        return tenant_id

    # ── paths ────────────────────────────────────────────────────────────

    def _task_dir(self, task_id: str) -> Path:
        return safe_child(self.root_dir, task_id)

    def _snapshot_file(self, task_id: str, snapshot_id: str) -> Path:
        validate_id(snapshot_id, "snapshot_id")
        return safe_child(self.root_dir, task_id, f"{snapshot_id}.json")

    @contextmanager
    def _task_lock(self, task_dir: Path) -> Iterator[None]:
        task_dir.mkdir(parents=True, exist_ok=True)
        lock = task_dir / ".lock"
        with open(lock, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_index(self, task_dir: Path) -> list[SnapshotMetadata]:
        index = task_dir / INDEX_FILE
        if not index.exists():
            return []
        with open(index, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return [SnapshotMetadata.from_dict(item) for item in data]

    # ── writes ───────────────────────────────────────────────────────────

    def write_snapshot(
        self,
        snapshot: Snapshot,
        audit_callback: Optional[AuditFn] = None,
    ) -> tuple[bool, str]:
        """Persist ``snapshot`` (audit-first, atomic, chained).

        Returns ``(True, "")`` or ``(False, reason)``. Refuses a snapshot whose
        ``tenant_id`` is not the bound tenant, a duplicate ``snapshot_id``, and a
        ``prev_snapshot_hash`` that does not equal the task's current chain
        head (a non-empty chain requires a correct link; the first snapshot
        may carry ``None``).
        """
        try:
            self._bind(snapshot.tenant_id)
        except ValueError as exc:
            return False, str(exc)

        try:
            task_dir = self._task_dir(snapshot.task_id)
            target = self._snapshot_file(snapshot.task_id, snapshot.snapshot_id)
        except ValueError as exc:
            return False, str(exc)

        payload = snapshot.to_dict()
        size_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))

        with self._task_lock(task_dir):
            index = self._read_index(task_dir)
            if any(m.snapshot_id == snapshot.snapshot_id for m in index) or target.exists():
                return False, f"snapshot {snapshot.snapshot_id} already exists (append-only)"
            head = index[-1].content_hash if index else None
            if snapshot.prev_snapshot_hash != head:
                return False, (
                    "chain link mismatch: prev_snapshot_hash must equal the current "
                    f"head ({head!r}) (fail-closed)"
                )
            seq = (index[-1].seq + 1) if index else 1

            # Audit FIRST (core chain); no commit → nothing on disk.
            details = content_free({
                "task_id": snapshot.task_id,
                "phase_id": snapshot.phase_id,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_type": snapshot.snapshot_type.value,
                "content_hash": snapshot.content_hash,
                "prev_snapshot_hash": snapshot.prev_snapshot_hash or "",
                "seq": seq,
                "size_bytes": size_bytes,
            })
            emit = audit_callback or self._audit
            try:
                audit_ref = emit(
                    EVENT_SNAPSHOT_CREATED, tenant_id=snapshot.tenant_id, details=details
                )
            except Exception as exc:  # fail-closed: writer refused / unavailable
                return False, f"audit write did not commit (fail-closed): {exc}"
            if not audit_ref:
                return False, "audit write did not commit (fail-closed)"

            try:
                _write_atomic(target, payload)
                meta = SnapshotMetadata.from_snapshot(snapshot, str(target), seq=seq)
                index.append(meta)
                _write_atomic(task_dir / INDEX_FILE, [m.to_dict() for m in index])  # type: ignore[arg-type]
            except OSError as exc:
                return False, f"Failed to write snapshot: {exc}"

        return True, ""

    # ── reads ────────────────────────────────────────────────────────────

    def list_tasks(self) -> list[str]:
        """Task ids that have at least one snapshot (sorted)."""
        tasks = []
        for child in sorted(self.root_dir.iterdir()):
            if child.is_dir() and (child / INDEX_FILE).exists():
                tasks.append(child.name)
        return tasks

    def list_snapshots(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: Optional[str] = None,
    ) -> tuple[list[SnapshotMetadata], str]:
        """Ordered (seq asc) metadata for ``task_id``, optionally one phase."""
        try:
            self._bind(tenant_id)
            task_dir = self._task_dir(task_id)
            if phase_id is not None:
                validate_id(phase_id, "phase_id")
        except ValueError as exc:
            return [], str(exc)
        if not task_dir.exists():
            return [], ""
        try:
            with self._task_lock(task_dir):
                index = self._read_index(task_dir)
        except (OSError, ValueError, KeyError) as exc:
            return [], f"Failed to list snapshots: {exc}"
        if phase_id is not None:
            index = [m for m in index if m.phase_id == phase_id]
        return sorted(index, key=lambda m: m.seq), ""

    def read_snapshot(
        self,
        tenant_id: str,
        task_id: str,
        snapshot_id: str,
    ) -> tuple[Optional[Snapshot], str]:
        """Load one snapshot; the content hash is re-verified on load."""
        try:
            self._bind(tenant_id)
            target = self._snapshot_file(task_id, snapshot_id)
        except ValueError as exc:
            return None, str(exc)
        if not target.exists():
            return None, f"Snapshot not found: {snapshot_id}"
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            snapshot = Snapshot.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return None, f"Failed to read snapshot {snapshot_id}: {exc}"
        if snapshot.tenant_id != self.tenant_id or snapshot.task_id != task_id:
            return None, f"Snapshot {snapshot_id} does not belong to {task_id} (fail-closed)"
        return snapshot, ""

    def get_latest_snapshot(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: Optional[str] = None,
    ) -> tuple[Optional[Snapshot], str]:
        """The chain head of ``task_id`` (or the last snapshot of ``phase_id``)."""
        index, error = self.list_snapshots(tenant_id, task_id, phase_id)
        if error:
            return None, error
        if not index:
            return None, "No snapshots found"
        return self.read_snapshot(tenant_id, task_id, index[-1].snapshot_id)

    def verify_snapshot_chain(
        self,
        tenant_id: str,
        task_id: str,
    ) -> tuple[bool, str]:
        """Re-hash every snapshot of ``task_id`` and verify the prev-hash links."""
        index, error = self.list_snapshots(tenant_id, task_id)
        if error:
            return False, error
        prev_hash: Optional[str] = None
        expected_seq = 1
        for meta in index:
            if meta.seq != expected_seq:
                return False, f"Chain gap at seq {expected_seq} (found {meta.seq})"
            snapshot, read_error = self.read_snapshot(tenant_id, task_id, meta.snapshot_id)
            if read_error:
                return False, f"Cannot read snapshot {meta.snapshot_id}: {read_error}"
            if snapshot.content_hash != meta.content_hash:
                return False, f"Index/content hash mismatch at snapshot {meta.snapshot_id}"
            if snapshot.prev_snapshot_hash != prev_hash:
                return False, f"Chain link broken at snapshot {meta.snapshot_id}"
            prev_hash = snapshot.content_hash
            expected_seq += 1
        return True, ""

    # ── archival ─────────────────────────────────────────────────────────

    def delete_snapshots_before(
        self,
        tenant_id: str,
        task_id: str,
        timestamp: str,
        audit_callback: Optional[AuditFn] = None,
    ) -> tuple[int, str]:
        """Archive (delete) snapshots older than ``timestamp`` — audited per file.

        Only a PREFIX of the chain may be pruned: the newest snapshot is always
        kept, so the chain head (and every later link) stays verifiable.
        """
        try:
            self._bind(tenant_id)
            task_dir = self._task_dir(task_id)
        except ValueError as exc:
            return 0, str(exc)
        if not task_dir.exists():
            return 0, ""
        emit = audit_callback or self._audit
        count = 0
        with self._task_lock(task_dir):
            index = self._read_index(task_dir)
            keep: list[SnapshotMetadata] = []
            prunable = True
            for meta in index:
                if prunable and meta.timestamp < timestamp and meta is not index[-1]:
                    try:
                        emit(EVENT_SNAPSHOT_ARCHIVED, tenant_id=tenant_id, details={
                            "task_id": task_id, "snapshot_id": meta.snapshot_id,
                            "content_hash": meta.content_hash, "seq": meta.seq,
                        })
                    except Exception as exc:
                        return count, f"audit write did not commit (fail-closed): {exc}"
                    target = self._snapshot_file(task_id, meta.snapshot_id)
                    if target.exists():
                        target.unlink()
                    count += 1
                else:
                    prunable = False
                    keep.append(meta)
            if count:
                _write_atomic(task_dir / INDEX_FILE, [m.to_dict() for m in keep])  # type: ignore[arg-type]
        return count, ""


def snapshot_task_state(
    tenant_id: str,
    task_id: str,
    state: dict[str, Any],
    *,
    phase_id: str = "turn",
    snapshot_type: SnapshotType = SnapshotType.PHASE_CHECKPOINT,
    base_commit: Optional[str] = None,
    worktree_path: Optional[str] = None,
    store: Optional[EventStore] = None,
) -> tuple[Optional[Snapshot], str]:
    """Producer entry point: chain a new snapshot of ``state`` onto ``task_id``.

    Looks up the task's current chain head, creates a ``Snapshot`` linked to it
    and writes it audit-first. ``state`` must be content-free of PII (the schema
    rejects e-mail/phone/card shapes) and ≤ 50 MB.

    Intended call site: the console task worker, right after a task turn
    finishes (see ``docs/claude-ref/infinite-session.md`` § Producer).
    """
    try:
        es = store or EventStore(tenant_id)
        latest, _ = es.get_latest_snapshot(tenant_id, task_id)
        snapshot = Snapshot.create(
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id=phase_id,
            state_dict=state,
            snapshot_type=snapshot_type,
            prev_snapshot_hash=latest.content_hash if latest else None,
            base_commit=base_commit,
            worktree_path=worktree_path,
        )
    except ValueError as exc:
        return None, str(exc)
    ok, error = es.write_snapshot(snapshot)
    if not ok:
        return None, error
    return snapshot, ""
