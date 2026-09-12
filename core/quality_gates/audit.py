"""Audit trail integration for Quality Gates System (ADR-0688).

Logs gate decisions to audit chain with hash-chaining.
"""

import hashlib
import json
from datetime import datetime
from typing import Optional
import logging
import duckdb

from .models import GateResult, AuditEvent, EventType

logger = logging.getLogger(__name__)


class QualityGateAuditLogger:
    """Logs quality gate decisions to audit trail."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        """Initialize audit logger.

        Args:
            conn: DuckDB connection (assumes schema is initialized)
        """
        self.conn = conn

    def compute_event_hash(self, event_data: dict, prior_hash: Optional[str] = None) -> str:
        """Compute SHA256 hash of event.

        Args:
            event_data: Event data to hash
            prior_hash: Previous event hash (for chain linking)

        Returns:
            SHA256 hex digest
        """
        # Include prior_hash in computation for chain linking
        data_to_hash = json.dumps(event_data, sort_keys=True)
        if prior_hash:
            data_to_hash = f"{prior_hash}:{data_to_hash}"

        return hashlib.sha256(data_to_hash.encode()).hexdigest()

    def get_last_event_hash(self, tenant_id: str) -> Optional[str]:
        """Get hash of most recent event for tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            Hash of last event, or None if no events exist
        """
        query = (
            "SELECT event_hash FROM gate_events "
            "WHERE tenant_id = ? ORDER BY timestamp DESC LIMIT 1"
        )
        result = self.conn.execute(query, [tenant_id]).fetchall()

        if result:
            return result[0][0]
        return None

    def write_gate_event(
        self,
        result: GateResult,
    ) -> str:
        """Write a gate decision to audit chain.

        Args:
            result: GateResult to log

        Returns:
            Event hash

        Raises:
            ValueError: If tenant_id is missing
        """
        if not result.tenant_id:
            raise ValueError("GateResult.tenant_id is required")

        # Get prior hash for chain linking
        prior_hash = self.get_last_event_hash(result.tenant_id)

        # Prepare event data
        event_data = {
            "event_type": EventType.QUALITY_GATE_DECIDED.value,
            "tenant_id": result.tenant_id,
            "gate_name": result.gate_name,
            "artifact_id": result.artifact_id,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "findings_count": len(result.findings) if result.findings else 0,
        }

        # Compute event hash
        event_hash = self.compute_event_hash(event_data, prior_hash)

        # Prepare timestamp
        timestamp = result.timestamp or datetime.utcnow().isoformat() + "Z"

        # Generate event ID
        event_id = f"{result.artifact_id}:{event_hash[:16]}"

        # Insert into gate_events table
        self.conn.execute(
            "INSERT INTO gate_events "
            "(id, timestamp, tenant_id, gate_name, artifact_id, verdict, confidence, reason, "
            "event_hash, prior_hash, findings_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                event_id,
                timestamp,
                result.tenant_id,
                result.gate_name,
                result.artifact_id,
                result.verdict.value,
                result.confidence,
                result.reason,
                event_hash,
                prior_hash,
                len(result.findings) if result.findings else 0,
            ],
        )

        logger.info(
            f"Logged gate event: {result.gate_name} {result.artifact_id} "
            f"{result.verdict.value} (hash={event_hash[:16]})"
        )

        return event_hash

    def create_audit_event(
        self,
        result: GateResult,
        event_hash: str,
        prior_hash: Optional[str] = None,
    ) -> AuditEvent:
        """Create an AuditEvent from a GateResult.

        Args:
            result: GateResult
            event_hash: Computed event hash
            prior_hash: Prior event hash (for chain linking)

        Returns:
            AuditEvent
        """
        timestamp = result.timestamp or datetime.utcnow().isoformat() + "Z"

        return AuditEvent(
            event_type=EventType.QUALITY_GATE_DECIDED,
            tenant_id=result.tenant_id,
            timestamp=timestamp,
            gate_name=result.gate_name,
            artifact_id=result.artifact_id,
            verdict=result.verdict,
            confidence=result.confidence,
            reason=result.reason,
            event_hash=event_hash,
            prior_hash=prior_hash,
            findings_count=len(result.findings) if result.findings else 0,
        )

    def verify_chain(self, tenant_id: str) -> bool:
        """Verify gate_events hash-chain integrity for tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            True if chain is valid, False otherwise
        """
        query = (
            "SELECT event_hash, prior_hash FROM gate_events "
            "WHERE tenant_id = ? ORDER BY timestamp ASC"
        )
        result = self.conn.execute(query, [tenant_id]).fetchall()

        prev_hash = None
        for event_hash, prior_hash in result:
            if prior_hash and prior_hash != prev_hash:
                logger.error(
                    f"Chain verification failed: prior_hash {prior_hash} != prev_hash {prev_hash}"
                )
                return False
            prev_hash = event_hash

        logger.info(f"Chain verification passed for tenant {tenant_id} ({len(result)} events)")
        return True
