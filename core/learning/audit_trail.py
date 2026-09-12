"""
ADR-0665: Audit-First Learning — Immutable Event Log + Hash Chain.

All learning decisions (generation, usage, feedback, weight updates) are:
1. Immutable (append-only)
2. Hash-chained (ADR-0232/0233)
3. Tenant-scoped (ADR-0007)
4. Verifiable (daily integrity check)
5. Privacy-respecting (GDPR Art. 5, 20, 30)
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import os

logger = logging.getLogger(__name__)


# ============================================================================
# AUDIT EVENT TYPES (IMMUTABLE)
# ============================================================================

@dataclass(frozen=True)
class GenerationEvent:
    """Skill generation event."""
    skill_id: str
    generation_id: str
    timestamp: str
    data_sources_used: List[str]
    phase_events: List[Dict[str, Any]]
    final_loss_vector: Dict[str, float]
    output_zip_hash: str
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UsageEvent:
    """Skill execution event."""
    skill_id: str
    execution_id: str
    timestamp: str
    success: bool
    error_if_any: Optional[str] = None
    data_source_used: Optional[str] = None
    execution_time_ms: float = 0.0
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackEvent:
    """User feedback event (privacy-respecting)."""
    feedback_id: str
    skill_id: str
    timestamp: str
    signal: float  # −1.0 to +1.0
    dimension: str  # overall | quality | performance | relevance
    user_id_masked: str  # sha256(email)[:16]
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearningEvent:
    """Daemon decision event."""
    learning_id: str
    event_type: str  # weight_updated | regeneration_queued | convergence_detected
    timestamp: str
    input_events: List[str]  # triggering event IDs
    decision: Dict[str, Any]
    before_state: Dict[str, float]
    after_state: Dict[str, float]
    confidence: float  # [0, 1]
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditRecord:
    """Hash-chained audit record (the actual on-disk format)."""
    event_id: str
    event_type: str  # generation | usage | feedback | learning
    timestamp: str
    event_data: Dict[str, Any]
    hash: str  # sha256 of (prev_hash + event_data)
    prev_hash: str  # hash of previous event (forms chain)
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# AUDIT TRAIL (Immutable, Hash-Chained)
# ============================================================================

class AuditTrail:
    """
    Immutable append-only audit log with hash-chaining.

    Design:
    - Events written to file (one JSON per line)
    - Each event has sha256(prev_hash + data)
    - Verification: replay chain, check hashes
    - Retention: 90-day archival policy
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.audit_dir = Path(
            os.environ.get(
                "CORVIN_HOME",
                os.path.expanduser("~/.corvin"),
            )
        ) / "tenants" / tenant_id / "global" / "learning"

        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.audit_dir / "audit_trail.jsonl"
        self.chain_hash = "genesis"  # Start of chain

        # Load existing chain
        self._load_chain_hash()

    def _load_chain_hash(self) -> None:
        """Load the last hash from audit file (for resuming)."""
        if not self.audit_file.exists():
            self.chain_hash = "genesis"
            return

        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        self.chain_hash = record.get("hash", "genesis")
        except Exception as e:
            logger.warning(f"Failed to load chain hash: {e}")
            self.chain_hash = "genesis"

    async def write_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        event_id: Optional[str] = None,
    ) -> str:
        """
        Write event to audit trail (immutable).

        Returns: hash of this event (for future references)
        """
        if event_id is None:
            event_id = f"{event_type}-{datetime.utcnow().isoformat()}"

        timestamp = datetime.utcnow().isoformat()

        # Compute hash: sha256(prev_hash + event_data)
        data_to_hash = json.dumps(event_data, sort_keys=True)
        combined = f"{self.chain_hash}{data_to_hash}"
        event_hash = hashlib.sha256(combined.encode()).hexdigest()

        # Create record
        record = AuditRecord(
            event_id=event_id,
            event_type=event_type,
            timestamp=timestamp,
            event_data=event_data,
            hash=event_hash,
            prev_hash=self.chain_hash,
            tenant_id=self.tenant_id,
        )

        # Write to file (append-only)
        try:
            with open(self.audit_file, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
                f.flush()

            # Update chain
            self.chain_hash = event_hash
            return event_hash

        except IOError as e:
            logger.error(f"Failed to write audit event: {e}")
            raise

    async def verify_chain(self) -> bool:
        """
        Verify hash chain integrity (for compliance audits).

        Returns: True if chain is unbroken
        """
        if not self.audit_file.exists():
            return True  # No events to verify

        try:
            chain_hash = "genesis"
            with open(self.audit_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    record = json.loads(line)
                    prev_hash = record.get("prev_hash")
                    event_hash = record.get("hash")
                    event_data = record.get("event_data", {})

                    # Verify prev_hash matches
                    if prev_hash != chain_hash:
                        logger.error(f"Chain broken at {record['event_id']}")
                        return False

                    # Verify event hash
                    data_to_hash = json.dumps(event_data, sort_keys=True)
                    combined = f"{chain_hash}{data_to_hash}"
                    expected_hash = hashlib.sha256(combined.encode()).hexdigest()

                    if event_hash != expected_hash:
                        logger.error(f"Hash mismatch at {record['event_id']}")
                        return False

                    chain_hash = event_hash

            return True

        except Exception as e:
            logger.error(f"Verification error: {e}")
            return False

    async def query_skill_lifecycle(self, skill_id: str) -> List[Dict[str, Any]]:
        """Query all events for a skill (generation → usage → feedback → learning)."""
        if not self.audit_file.exists():
            return []

        events = []
        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    record = json.loads(line)
                    event_data = record.get("event_data", {})

                    if event_data.get("skill_id") == skill_id or skill_id in event_data.get("data_sources_used", []):
                        events.append(record)

            return events
        except Exception as e:
            logger.error(f"Query error: {e}")
            return []

    async def export_for_compliance(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        GDPR Art. 30 compliance export.

        Includes: all generations, all usages, all feedback, all decisions.
        Excludes: user names, email addresses, prompts.
        """
        if start_time is None:
            start_time = datetime.utcnow() - timedelta(days=90)
        if end_time is None:
            end_time = datetime.utcnow()

        if not self.audit_file.exists():
            return {"events": [], "tenant_id": self.tenant_id}

        events = []
        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    record = json.loads(line)
                    timestamp_str = record.get("timestamp", "")
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

                    if start_time <= timestamp <= end_time:
                        events.append(record)

            return {
                "tenant_id": self.tenant_id,
                "export_timestamp": datetime.utcnow().isoformat(),
                "event_count": len(events),
                "period_start": start_time.isoformat(),
                "period_end": end_time.isoformat(),
                "events": events,
            }

        except Exception as e:
            logger.error(f"Export error: {e}")
            return {"events": [], "tenant_id": self.tenant_id}

    async def get_chain_status(self) -> Dict[str, Any]:
        """Get current chain status (height, last hash, integrity)."""
        if not self.audit_file.exists():
            return {
                "height": 0,
                "last_hash": "genesis",
                "integrity_verified": True,
                "last_verified": datetime.utcnow().isoformat(),
            }

        height = 0
        last_hash = "genesis"

        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if line.strip():
                        height += 1
                        record = json.loads(line)
                        last_hash = record.get("hash", "genesis")

            integrity_ok = await self.verify_chain()

            return {
                "height": height,
                "last_hash": last_hash,
                "integrity_verified": integrity_ok,
                "last_verified": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Status error: {e}")
            return {
                "height": 0,
                "last_hash": "genesis",
                "integrity_verified": False,
                "error": str(e),
            }

    async def retention_policy(self, max_age_days: int = 90) -> int:
        """
        Archive events older than max_age_days.

        Returns: number of events archived
        """
        if not self.audit_file.exists():
            return 0

        cutoff_time = datetime.utcnow() - timedelta(days=max_age_days)
        events_to_keep = []
        archived_count = 0

        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    record = json.loads(line)
                    timestamp_str = record.get("timestamp", "")
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

                    if timestamp >= cutoff_time:
                        events_to_keep.append(line)
                    else:
                        archived_count += 1

            # Rewrite file (keep only recent events)
            with open(self.audit_file, "w") as f:
                for line in events_to_keep:
                    f.write(line)
                f.flush()

            # Update chain hash
            self._load_chain_hash()

            logger.info(f"Archived {archived_count} events (>= {max_age_days} days old)")
            return archived_count

        except Exception as e:
            logger.error(f"Retention policy error: {e}")
            return 0
