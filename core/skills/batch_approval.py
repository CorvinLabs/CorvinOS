"""L5 k=2: Batch Approval Manager — Atomic Batch Operations on Multiple Approvals.

ADR-0577: Batch Approval Manager
- list_pending_batches(skill_id) -> Dict of batch metadata
- operator_batch_approve(batch_id, operator_id) -> applies all members atomically
- operator_batch_reject(batch_id, operator_id, reason) -> rejects all together
- operator_batch_revoke(batch_id, operator_id) -> revokes all together
- Audit trail: every batch operation logged
- Failure handling: atomic approval, non-atomic application (marked PARTIAL_SUCCESS)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import uuid
import json
from pathlib import Path
import threading
from enum import Enum

logger = logging.getLogger(__name__)


class BatchStatus(str, Enum):
    """Batch operation status."""
    PENDING = "pending"  # All members pending
    APPROVED = "approved"  # All members approved
    REJECTED = "rejected"  # All members rejected
    REVOKED = "revoked"  # All members revoked
    PARTIAL_SUCCESS = "partial_success"  # Some applied, some failed


@dataclass
class BatchMember:
    """A single approval within a batch."""
    approval_id: str
    skill_id: str
    metric_name: str
    decision: str  # pending, approved, rejected, revoked
    timestamp: str


@dataclass
class BatchApprovalRecord:
    """A batch of related approvals (immutable)."""
    batch_id: str
    skill_id: str
    member_ids: List[str]  # approval_ids in batch
    confidence_range: Tuple[float, float]  # (min, max) confidence of members
    status: BatchStatus
    operator_id: Optional[str] = None
    created_timestamp: str = ""
    completed_timestamp: Optional[str] = None
    reason: Optional[str] = None  # For rejection/revoke
    audit_event_id: str = ""


class BatchApprovalManager:
    """
    L5 k=2: Batch Approval Manager.

    Manages atomic approval operations on multiple related approvals.
    Constraints:
    1. Batch creation is automatic (pending approvals grouped by skill + time window)
    2. Atomic approval: all members approved together or none
    3. Audit-first: every batch operation logged before state mutation
    4. Non-atomic application: if some apply fails, marked PARTIAL_SUCCESS (not rolled back)
    5. Thread-safe: all state mutations under lock
    """

    def __init__(
        self,
        approval_gate,
        tenant_id: str = "_default",
        batch_window_minutes: int = 30,
        corvin_home: str = None,
    ):
        """
        Initialize batch approval manager.

        Args:
            approval_gate: OperatorApprovalGate instance
            tenant_id: Tenant ID
            batch_window_minutes: Group approvals within this window (default 30 min)
            corvin_home: Path to ~/.corvin
        """
        self.approval_gate = approval_gate
        self.tenant_id = tenant_id
        self.batch_window_minutes = batch_window_minutes
        self.audit_backend = approval_gate.audit_backend

        # Persistence: path to batches.jsonl
        if corvin_home is None:
            import os
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        self.corvin_home = Path(corvin_home)
        self.batches_file = self.corvin_home / "tenants" / tenant_id / "skills" / "batches.jsonl"

        # Thread safety
        self._lock = threading.RLock()

        # In-memory batch tracking
        self.batches: Dict[str, BatchApprovalRecord] = {}  # batch_id -> record
        self.batch_history: List[BatchApprovalRecord] = []  # Immutable append-only log

        # Load persisted batches from disk
        self._load_persisted_batches()

    def _load_persisted_batches(self) -> None:
        """Load batch history from disk (recovery after restart)."""
        if not self.batches_file.exists():
            return

        try:
            with open(self.batches_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        # Reconstruct BatchApprovalRecord
                        record = BatchApprovalRecord(
                            batch_id=data["batch_id"],
                            skill_id=data["skill_id"],
                            member_ids=data["member_ids"],
                            confidence_range=tuple(data["confidence_range"]),
                            status=BatchStatus(data["status"]),
                            operator_id=data.get("operator_id"),
                            created_timestamp=data.get("created_timestamp", ""),
                            completed_timestamp=data.get("completed_timestamp"),
                            reason=data.get("reason"),
                            audit_event_id=data.get("audit_event_id", ""),
                        )
                        self.batch_history.append(record)
                        if record.status == BatchStatus.PENDING:
                            self.batches[record.batch_id] = record
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.warning(f"[Batch Approval] Failed to load batch record: {e}")
        except Exception as e:
            logger.error(f"[Batch Approval] Failed to load persisted batches: {e}")

    def _persist_batch(self, record: BatchApprovalRecord) -> None:
        """Append batch to disk (immutable log)."""
        try:
            self.batches_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.batches_file, "a") as f:
                data = {
                    "batch_id": record.batch_id,
                    "skill_id": record.skill_id,
                    "member_ids": record.member_ids,
                    "confidence_range": list(record.confidence_range),
                    "status": record.status.value,
                    "operator_id": record.operator_id,
                    "created_timestamp": record.created_timestamp,
                    "completed_timestamp": record.completed_timestamp,
                    "reason": record.reason,
                    "audit_event_id": record.audit_event_id,
                }
                json_line = json.dumps(data, default=str)
                f.write(json_line + "\n")
        except Exception as e:
            logger.error(f"[Batch Approval] Failed to persist batch {record.batch_id}: {e}")

    def list_pending_batches(self, skill_id: Optional[str] = None) -> Dict[str, Dict]:
        """
        List all pending batches (optionally filtered by skill).

        Args:
            skill_id: Optional filter by skill

        Returns:
            Dict[batch_id] -> {member_ids, confidence_range, created_timestamp, member_count}
        """
        with self._lock:
            result = {}
            for batch_id, record in self.batches.items():
                if skill_id and record.skill_id != skill_id:
                    continue
                if record.status != BatchStatus.PENDING:
                    continue

                result[batch_id] = {
                    "skill_id": record.skill_id,
                    "member_ids": record.member_ids,
                    "member_count": len(record.member_ids),
                    "confidence_range": record.confidence_range,
                    "created_timestamp": record.created_timestamp,
                }

            return result

    def _create_batch_for_skill(self, skill_id: str) -> Optional[str]:
        """
        Create a batch from all pending approvals for a skill within time window.

        Called automatically when new pending approval arrives (not exposed to API).

        Args:
            skill_id: Skill to batch

        Returns:
            batch_id if batch created, None if no pending approvals
        """
        with self._lock:
            pending = self.approval_gate.get_pending_approvals(skill_id=skill_id)
            if not pending:
                return None

            # Filter by time window
            now = datetime.utcnow().replace(tzinfo=None)
            cutoff = now - __import__('datetime').timedelta(minutes=self.batch_window_minutes)

            members = []
            confidences = []
            for p in pending:
                # Parse timestamp from approval record
                ts_str = p.scrubbed_alert.timestamp.replace("Z", "")
                try:
                    ts = datetime.fromisoformat(ts_str).replace(tzinfo=None)
                    if ts >= cutoff:
                        members.append(p.approval_id)
                        confidences.append(p.scrubbed_alert.confidence)
                except (ValueError, AttributeError):
                    # Skip if timestamp parsing fails
                    pass

            if not members:
                return None

            # Create batch
            batch_id = str(uuid.uuid4())
            min_conf = min(confidences) if confidences else 0.0
            max_conf = max(confidences) if confidences else 1.0

            batch = BatchApprovalRecord(
                batch_id=batch_id,
                skill_id=skill_id,
                member_ids=members,
                confidence_range=(min_conf, max_conf),
                status=BatchStatus.PENDING,
                created_timestamp=now.isoformat() + "Z",
            )

            # AUDIT-FIRST
            audit_event = {
                "tenant_id": self.tenant_id,
                "event_type": "batch_approval_created",
                "batch_id": batch_id,
                "skill_id": skill_id,
                "member_count": len(members),
                "confidence_range": [min_conf, max_conf],
            }

            try:
                event_id = self.audit_backend.write_event(audit_event)
                batch.audit_event_id = str(event_id) if event_id else ""
            except Exception as e:
                logger.error(f"[Batch Approval] Failed to audit batch creation: {e}")
                raise RuntimeError(f"[Batch Approval] FATAL: audit failed; batch NOT created (fail-closed).")

            # State mutation AFTER successful audit
            self.batches[batch_id] = batch
            self._persist_batch(batch)

            logger.info(f"[Batch Approval] Created batch {batch_id} with {len(members)} members for {skill_id}")
            return batch_id

    def operator_batch_approve(
        self,
        batch_id: str,
        operator_id: str,
    ) -> Tuple[bool, Dict]:
        """
        Operator approves all members of a batch (AUDIT-FIRST, ATOMIC).

        Constraint #2: Atomic approval — all members approved or none.

        Args:
            batch_id: Batch ID
            operator_id: Who is approving

        Returns:
            (success: bool, result: {approved_count, failed_count, failed_approvals})

        Raises:
            ValueError if inputs invalid
            RuntimeError if audit fails (fail-closed)
        """
        with self._lock:
            if batch_id not in self.batches:
                logger.warning(f"[Batch Approval] Batch {batch_id} not found")
                return False, {"error": "batch not found"}

            batch = self.batches[batch_id]
            if batch.status != BatchStatus.PENDING:
                logger.warning(f"[Batch Approval] Batch {batch_id} is {batch.status.value}, cannot approve")
                return False, {"error": f"batch is {batch.status.value}"}

            # AUDIT-FIRST: write event BEFORE state mutation
            audit_event = {
                "tenant_id": self.tenant_id,
                "event_type": "batch_approval_granted",
                "batch_id": batch_id,
                "operator_id": operator_id,
                "skill_id": batch.skill_id,
                "member_count": len(batch.member_ids),
            }

            try:
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"[Batch Approval] Failed to audit batch approval: {e}")
                raise RuntimeError(
                    f"[Batch Approval] FATAL: audit failed; batch NOT approved (fail-closed)."
                )

            # State mutation: approve all members (atomically under lock)
            approved_count = 0
            failed_count = 0
            failed_approvals = []

            for approval_id in batch.member_ids:
                try:
                    success = self.approval_gate.operator_approve(
                        approval_id=approval_id,
                        operator_id=operator_id,
                    )
                    if success:
                        approved_count += 1
                    else:
                        failed_count += 1
                        failed_approvals.append(approval_id)
                except Exception as e:
                    logger.error(f"[Batch Approval] Failed to approve {approval_id}: {e}")
                    failed_count += 1
                    failed_approvals.append(approval_id)

            # Update batch status
            if failed_count == 0:
                batch.status = BatchStatus.APPROVED
                batch.operator_id = operator_id
                batch.completed_timestamp = datetime.utcnow().isoformat() + "Z"
                success = True
            else:
                # Partial success
                batch.status = BatchStatus.PARTIAL_SUCCESS
                batch.operator_id = operator_id
                batch.completed_timestamp = datetime.utcnow().isoformat() + "Z"
                success = True  # Approval decision is atomic; application is not

            self._persist_batch(batch)

            logger.info(
                f"[Batch Approval] Batch {batch_id} approved: "
                f"{approved_count} approved, {failed_count} failed"
            )

            return success, {
                "approved_count": approved_count,
                "failed_count": failed_count,
                "failed_approvals": failed_approvals,
            }

    def operator_batch_reject(
        self,
        batch_id: str,
        operator_id: str,
        reason: str = "",
    ) -> Tuple[bool, Dict]:
        """
        Operator rejects all members of a batch (AUDIT-FIRST).

        Args:
            batch_id: Batch ID
            operator_id: Who is rejecting
            reason: Optional explanation

        Returns:
            (success: bool, result: {rejected_count, failed_count, failed_rejections})
        """
        with self._lock:
            if batch_id not in self.batches:
                logger.warning(f"[Batch Approval] Batch {batch_id} not found")
                return False, {"error": "batch not found"}

            batch = self.batches[batch_id]
            if batch.status != BatchStatus.PENDING:
                logger.warning(f"[Batch Approval] Batch {batch_id} is {batch.status.value}, cannot reject")
                return False, {"error": f"batch is {batch.status.value}"}

            # AUDIT-FIRST
            audit_event = {
                "tenant_id": self.tenant_id,
                "event_type": "batch_approval_denied",
                "batch_id": batch_id,
                "operator_id": operator_id,
                "skill_id": batch.skill_id,
                "member_count": len(batch.member_ids),
                "reason": reason,
            }

            try:
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"[Batch Approval] Failed to audit batch rejection: {e}")
                raise RuntimeError(
                    f"[Batch Approval] FATAL: audit failed; batch NOT rejected (fail-closed)."
                )

            # State mutation: reject all members
            rejected_count = 0
            failed_count = 0
            failed_rejections = []

            for approval_id in batch.member_ids:
                try:
                    success = self.approval_gate.operator_reject(
                        approval_id=approval_id,
                        operator_id=operator_id,
                        reason=reason,
                    )
                    if success:
                        rejected_count += 1
                    else:
                        failed_count += 1
                        failed_rejections.append(approval_id)
                except Exception as e:
                    logger.error(f"[Batch Approval] Failed to reject {approval_id}: {e}")
                    failed_count += 1
                    failed_rejections.append(approval_id)

            # Update batch status
            batch.status = BatchStatus.REJECTED
            batch.operator_id = operator_id
            batch.reason = reason
            batch.completed_timestamp = datetime.utcnow().isoformat() + "Z"

            self._persist_batch(batch)

            logger.info(
                f"[Batch Approval] Batch {batch_id} rejected: "
                f"{rejected_count} rejected, {failed_count} failed"
            )

            return True, {
                "rejected_count": rejected_count,
                "failed_count": failed_count,
                "failed_rejections": failed_rejections,
            }

    def operator_batch_revoke(
        self,
        batch_id: str,
        operator_id: str,
        reason: str = "",
    ) -> Tuple[bool, Dict]:
        """
        Operator revokes all members of an approved batch (AUDIT-FIRST).

        Args:
            batch_id: Batch ID
            operator_id: Who is revoking
            reason: Optional explanation

        Returns:
            (success: bool, result: {revoked_count, failed_count, failed_revokes})
        """
        with self._lock:
            if batch_id not in self.batches:
                # Check history
                batch = None
                for b in self.batch_history:
                    if b.batch_id == batch_id:
                        batch = b
                        break
                if not batch:
                    logger.warning(f"[Batch Approval] Batch {batch_id} not found")
                    return False, {"error": "batch not found"}
            else:
                batch = self.batches[batch_id]

            if batch.status != BatchStatus.APPROVED and batch.status != BatchStatus.PARTIAL_SUCCESS:
                logger.warning(
                    f"[Batch Approval] Batch {batch_id} is {batch.status.value}, "
                    f"cannot revoke (must be APPROVED or PARTIAL_SUCCESS)"
                )
                return False, {"error": f"batch is {batch.status.value}"}

            # AUDIT-FIRST
            audit_event = {
                "tenant_id": self.tenant_id,
                "event_type": "batch_approval_revoked",
                "batch_id": batch_id,
                "operator_id": operator_id,
                "skill_id": batch.skill_id,
                "member_count": len(batch.member_ids),
                "reason": reason,
            }

            try:
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"[Batch Approval] Failed to audit batch revoke: {e}")
                raise RuntimeError(
                    f"[Batch Approval] FATAL: audit failed; batch NOT revoked (fail-closed)."
                )

            # State mutation: revoke all members
            revoked_count = 0
            failed_count = 0
            failed_revokes = []

            for approval_id in batch.member_ids:
                try:
                    success = self.approval_gate.operator_revoke(
                        approval_id=approval_id,
                        operator_id=operator_id,
                        reason=reason,
                    )
                    if success:
                        revoked_count += 1
                    else:
                        failed_count += 1
                        failed_revokes.append(approval_id)
                except Exception as e:
                    logger.error(f"[Batch Approval] Failed to revoke {approval_id}: {e}")
                    failed_count += 1
                    failed_revokes.append(approval_id)

            # Update batch status
            batch.status = BatchStatus.REVOKED
            batch.operator_id = operator_id
            batch.reason = reason
            batch.completed_timestamp = datetime.utcnow().isoformat() + "Z"

            self._persist_batch(batch)

            # Remove from active batches
            if batch_id in self.batches:
                del self.batches[batch_id]

            logger.warning(
                f"[Batch Approval] Batch {batch_id} revoked: "
                f"{revoked_count} revoked, {failed_count} failed"
            )

            return True, {
                "revoked_count": revoked_count,
                "failed_count": failed_count,
                "failed_revokes": failed_revokes,
            }

    def get_batch_status(self, batch_id: str) -> Optional[BatchApprovalRecord]:
        """
        Get status of a specific batch.

        Args:
            batch_id: Batch ID

        Returns:
            BatchApprovalRecord if found, None otherwise
        """
        with self._lock:
            # Check pending
            if batch_id in self.batches:
                return self.batches[batch_id]

            # Check history
            for b in self.batch_history:
                if b.batch_id == batch_id:
                    return b

        return None
