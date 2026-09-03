"""Event Persistence — disk I/O + audit integration (ADR-0314).

Every ADR-0314 learning event is written to the CORE hash-chained audit
writer FIRST (the same chain the boot tripwire verifies, ADR-0232/0233) and
only then to the date-partitioned disk record. The chain write is fail-closed:
if the core writer is unavailable or the record did not commit, ``write_event``
raises and nothing is written to disk.

The chain carries CONTENT-FREE metadata only (event_id, event_type, skill_name,
session_id, tags) — never the payload (GDPR Art. 5).

The store is BOUND to one tenant (ADR-0007): every read/write/cleanup checks
the tenant argument against the bound tenant and rejects a mismatch.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from core.tenants.validation import validate_tenant_id

from .event_schema import LearningEvent, LearningEventType

TOMBSTONE_EVENT_TYPE = "learning.tombstone"


# ── Core audit writer resolution (shared by the learning subsystem) ──────────


def _resolve_core_audit():
    """Return the ``audit`` module of the core hash-chained writer.

    Mirrors ``corvin_plugins.bootstrap._default_audit_emit``: the writer is the
    bare leaf module ``operator/bridges/shared/audit.py`` (importable once
    ``corvin_console`` put that directory on ``sys.path``). If it is not on
    the path yet, add the source-tree location. ``operator/`` has no
    ``__init__.py`` and always loses to the stdlib ``operator`` module, so a
    dotted import can never work here.

    Raises:
        RuntimeError: if the writer cannot be imported, or its forge
            dependency is missing (then ``audit_event`` is a silent no-op,
            which is exactly the fail-open ADR-0232 forbids).
    """
    try:
        import audit as _audit  # type: ignore[import-not-found]
    except ImportError:
        shared = Path(__file__).resolve().parents[2] / "operator" / "bridges" / "shared"
        if shared.is_dir() and str(shared) not in sys.path:
            sys.path.insert(0, str(shared))
        try:
            import audit as _audit  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("core audit writer unavailable") from exc
    if not hasattr(_audit, "audit_event") or getattr(_audit, "_se", None) is None:
        raise RuntimeError("core audit writer unavailable")
    return _audit


def _tail_contains(path: Path, needle: str, window: int = 65536) -> bool:
    """True if ``needle`` occurs in the last ``window`` bytes of ``path``."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    with open(path, "rb") as fh:
        fh.seek(max(0, size - window))
        return needle.encode() in fh.read()


def core_audit_event(
    event_type: str, *, tenant_id: str, details: dict[str, Any], user: str = ""
) -> str:
    """Write one CONTENT-FREE record to the core hash-chained audit writer.

    Returns the ``audit_ref`` (uuid4) that was stamped into the record so the
    caller can persist it as the chain reference.

    Fail-closed (ADR-0232/0233): raises ``RuntimeError`` when the writer is
    unavailable OR when the record did not commit. ``audit.audit_event`` is
    "silent on failure" by contract (it swallows I/O errors and the tenant
    context mismatch), so commit is verified by reading the chain tail back —
    a swallowed failure must never be reported as an audited event.
    """
    validate_tenant_id(tenant_id)
    _audit = _resolve_core_audit()
    audit_ref = str(uuid4())
    body = dict(details)
    body["audit_ref"] = audit_ref
    _audit.audit_event(event_type, user=user or "", details=body, tenant_id=tenant_id)
    path = Path(_audit.audit_path())
    if not _tail_contains(path, audit_ref):
        raise RuntimeError(
            f"core audit write did not commit for {event_type} (tenant {tenant_id!r})"
        )
    return audit_ref


# ── Store ───────────────────────────────────────────────────────────────────


class EventStore:
    """Persist learning events to disk with audit trail (tenant-bound)."""

    def __init__(self, tenant_id: str):
        """Initialize store.

        Args:
            tenant_id: Tenant identifier (e.g., "_default")

        Raises:
            ValueError: If tenant_id is invalid
        """
        from core.paths import tenant_learning_dir, tenant_audit_file

        self.tenant_id = validate_tenant_id(tenant_id)
        self.events_dir = tenant_learning_dir(tenant_id) / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)

        self.audit_path = tenant_audit_file(tenant_id)

    # ── tenant binding ──────────────────────────────────────────────────

    def _bind(self, tenant_id: Optional[str]) -> str:
        """Reject any tenant that is not the one this store is bound to."""
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("tenant_id is required (GDPR Art. 32)")
        validate_tenant_id(tenant_id)
        if tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: store is bound to {self.tenant_id!r}, got {tenant_id!r}"
            )
        return tenant_id

    # ── write ───────────────────────────────────────────────────────────

    async def write_event(self, event: LearningEvent, tenant_id: str) -> str:
        """Write event to the audit chain, then to disk.

        Args:
            event: Learning event to persist
            tenant_id: Tenant ID (must equal the store's bound tenant)

        Returns:
            audit_ref (hash-chain reference persisted in the disk record)

        Raises:
            ValueError: If tenant_id mismatch / empty
            RuntimeError: If the core audit writer is unavailable or the
                chain write did not commit (nothing is written to disk then)
        """
        self._bind(tenant_id)
        if event.tenant_id != tenant_id:
            raise ValueError(
                f"Tenant mismatch: event.tenant_id={event.tenant_id!r}, expected {tenant_id!r}"
            )

        # 1. Audit chain FIRST (content-free metadata; never the payload)
        audit_ref = core_audit_event(
            f"learning.{event.event_type.value}",
            tenant_id=tenant_id,
            details={
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "skill_name": event.skill_name,
                "session_id": event.session_id,
                "tags": list(event.tags),
            },
        )

        # 2. Disk record (date-partitioned), carrying the chain reference
        event_dict = event.to_audit_event()
        event_dict["audit_ref"] = audit_ref
        events_file = self.events_dir / f"{event.timestamp_utc.date().isoformat()}.jsonl"
        with open(events_file, "a") as f:
            f.write(json.dumps(event_dict) + "\n")

        return audit_ref

    # ── read ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_tombstone(event_dict: dict) -> bool:
        return event_dict.get("event_type") == TOMBSTONE_EVENT_TYPE

    async def read_events(
        self,
        *,
        tenant_id: str,
        event_type: Optional[LearningEventType] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[LearningEvent]:
        """Read events from store with filtering (newest first)."""
        self._bind(tenant_id)
        events: list[LearningEvent] = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(events) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)
                    if self._is_tombstone(event_dict):
                        continue

                    # Tenant isolation
                    if event_dict.get("tenant_id") != tenant_id:
                        continue

                    # Apply filters
                    if event_type and not event_dict["event_type"].endswith(f".{event_type.value}"):
                        continue
                    if skill_name and event_dict.get("skill_name") != skill_name:
                        continue
                    if session_id and event_dict.get("session_id") != session_id:
                        continue

                    event_ts = datetime.fromisoformat(event_dict["timestamp"].rstrip("Z"))
                    if since and event_ts < since:
                        continue

                    # Reconstruct event
                    event_type_value = event_dict["event_type"].replace("learning.", "")
                    event = LearningEvent(
                        event_type=LearningEventType(event_type_value),
                        tenant_id=event_dict["tenant_id"],
                        instance_id=event_dict["instance_id"],
                        user_id=event_dict.get("user_id"),
                        skill_name=event_dict.get("skill_name"),
                        session_id=event_dict["session_id"],
                        timestamp_utc=event_ts,
                        event_id=event_dict["event_id"],
                        payload=copy.deepcopy(event_dict.get("payload", {})),
                        audit_id=event_dict.get("audit_ref", event_dict.get("audit_id")),
                        tags=list(event_dict.get("tags", [])),
                    )
                    events.append(event)

                    if len(events) >= limit:
                        break

        return events

    # ── retention / erasure (atomic rewrites, audited) ──────────────────

    def _rewrite_partition(self, events_file: Path, remaining: list[str], tombstone: Optional[dict]) -> None:
        """Replace ``events_file`` atomically (temp + os.replace).

        A crash mid-write can never truncate the partition: the new content is
        fully written and fsynced to a sibling temp file first. An empty
        remainder without a tombstone removes the partition.
        """
        if not remaining and tombstone is None:
            events_file.unlink()
            return
        fd, tmp_name = tempfile.mkstemp(prefix=f".{events_file.stem}.", suffix=".tmp", dir=str(events_file.parent))
        try:
            with os.fdopen(fd, "w") as f:
                f.writelines(remaining)
                if tombstone is not None:
                    f.write(json.dumps(tombstone) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, events_file)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    async def cleanup_old_events(self, *, tenant_id: str, retention_days: int = 90) -> int:
        """Remove events older than the retention period (ADR-0319 default 90d).

        Partitions are rewritten atomically; the retention run is audited on
        the core chain with counts only.

        Returns:
            Number of events deleted
        """
        self._bind(tenant_id)
        cutoff_date = (datetime.utcnow() - timedelta(days=retention_days)).date()

        deleted_count = 0
        touched_files = 0

        for events_file in self.events_dir.glob("*.jsonl"):
            file_date = datetime.fromisoformat(events_file.stem).date()
            if file_date >= cutoff_date:
                continue

            remaining_lines: list[str] = []
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    if event_dict.get("tenant_id") == tenant_id:
                        deleted_count += 1
                    else:
                        remaining_lines.append(line)

            self._rewrite_partition(events_file, remaining_lines, tombstone=None)
            touched_files += 1

        core_audit_event(
            "learning.retention",
            tenant_id=tenant_id,
            details={
                "retention_days": retention_days,
                "deleted_count": deleted_count,
                "partitions_rewritten": touched_files,
            },
        )
        return deleted_count

    async def erase_user_events(self, *, tenant_id: str, user_id: str) -> int:
        """Erase every event of one user (GDPR Art. 17) from the disk partitions.

        Each affected partition is rewritten atomically and receives one
        tombstone line (erasure id + count — never the user id). The erasure
        is audited on the core chain with counts only. The immutable chain
        copies are content-free (they never carried the payload or user id),
        so nothing personal remains after this call.

        Returns:
            Number of events erased
        """
        self._bind(tenant_id)
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id is required")

        erasure_id = str(uuid4())
        erased = 0
        partitions = 0

        for events_file in sorted(self.events_dir.glob("*.jsonl")):
            remaining: list[str] = []
            removed_here = 0
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    if (
                        not self._is_tombstone(event_dict)
                        and event_dict.get("tenant_id") == tenant_id
                        and event_dict.get("user_id") == user_id
                    ):
                        removed_here += 1
                    else:
                        remaining.append(line)
            if removed_here == 0:
                continue
            tombstone = {
                "event_type": TOMBSTONE_EVENT_TYPE,
                "tenant_id": tenant_id,
                "erasure_id": erasure_id,
                "erased_count": removed_here,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            self._rewrite_partition(events_file, remaining, tombstone)
            erased += removed_here
            partitions += 1

        core_audit_event(
            "learning.erasure",
            tenant_id=tenant_id,
            details={
                "erasure_id": erasure_id,
                "erased_count": erased,
                "partitions_rewritten": partitions,
            },
        )
        return erased

    async def get_event_count(self, *, tenant_id: str) -> int:
        """Get total event count for the bound tenant."""
        self._bind(tenant_id)
        count = 0

        for events_file in self.events_dir.glob("*.jsonl"):
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    event_dict = json.loads(line)
                    if self._is_tombstone(event_dict):
                        continue
                    if event_dict.get("tenant_id") == tenant_id:
                        count += 1

        return count

    def _read_by_type(
        self,
        tenant_id: str,
        event_type_suffix: str,
        filters: dict,
        limit: int = 1000,
    ) -> list[dict]:
        """Generic reader for typed events (shared implementation).

        Args:
            tenant_id: Tenant ID (must equal the bound tenant)
            event_type_suffix: Event type to match (e.g. "decision.record")
            filters: Dict of {field_name: value} to match on payload/top-level
                     (keys starting with "payload." match fields in payload dict)
            limit: Max results

        Returns:
            Matching event payloads
        """
        self._bind(tenant_id)
        results: list[dict] = []

        for events_file in sorted(self.events_dir.glob("*.jsonl"), reverse=True):
            if len(results) >= limit:
                break

            with open(events_file) as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue

                    event_dict = json.loads(line)
                    if self._is_tombstone(event_dict):
                        continue

                    # Tenant + type filter
                    if event_dict.get("tenant_id") != tenant_id:
                        continue
                    if not event_dict["event_type"].endswith(event_type_suffix):
                        continue

                    # Apply optional filters
                    skip = False
                    for key, value in filters.items():
                        if key.startswith("payload."):
                            payload_key = key[len("payload."):]
                            if event_dict.get("payload", {}).get(payload_key) != value:
                                skip = True
                                break
                        else:
                            if event_dict.get(key) != value:
                                skip = True
                                break
                    if skip:
                        continue

                    results.append(event_dict.get("payload", {}))

                    if len(results) >= limit:
                        break

        return results

    async def read_decisions(
        self,
        *,
        tenant_id: str,
        session_id: Optional[str] = None,
        choice_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read decision records (ADR-0316)."""
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        if choice_type:
            filters["payload.choice_type"] = choice_type

        return self._read_by_type(tenant_id, "decision.record", filters, limit)

    async def read_outcomes(
        self,
        *,
        tenant_id: str,
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read outcome feedback records (ADR-0317)."""
        filters = {}
        if session_id:
            filters["session_id"] = session_id
        if decision_id:
            filters["payload.decision_id"] = decision_id

        return self._read_by_type(tenant_id, "outcome.observed", filters, limit)

    async def read_preferences(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str] = None,
        preference_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read preference change records (ADR-0318)."""
        filters = {}
        if user_id:
            filters["payload.user_id"] = user_id
        if preference_type:
            filters["payload.preference_type"] = preference_type

        return self._read_by_type(tenant_id, "preference.set", filters, limit)

    async def read_metrics(
        self,
        *,
        tenant_id: str,
        metric_type: Optional[str] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Read metric records (ADR-0320)."""
        filters = {}
        if metric_type:
            filters["payload.metric_type"] = metric_type
        if skill_name:
            filters["skill_name"] = skill_name
        if session_id:
            filters["session_id"] = session_id

        return self._read_by_type(tenant_id, "metric.aggregated", filters, limit)
