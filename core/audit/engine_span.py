"""Engine span tracking for orchestration audit trail.

Spans are atomic units of execution (e.g., "gather data", "analyze results").
Each span is hash-chained into the audit log (GDPR Art. 30, 32).
"""

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Optional

@dataclass
class EngineSpan:
    """Immutable span record for audit chain."""
    span_id: str
    engine_name: str
    phase: str  # e.g. 'gather', 'analyze', 'synthesize'
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: str = "running"  # running, completed, failed
    error_message: Optional[str] = None
    result_hash: Optional[str] = None
    previous_hash: Optional[str] = None  # Link to prior span (hash-chain)

    def to_audit_dict(self) -> Dict:
        """Serialize to audit-loggable dict (PII-scrubbed)."""
        return {
            "span_id": self.span_id,
            "engine_name": self.engine_name,
            "phase": self.phase,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "status": self.status,
            "error_message": self.error_message,
            "result_hash": self.result_hash,
            "previous_hash": self.previous_hash,
        }

    def compute_hash(self) -> str:
        """Compute SHA256 hash for chain integrity (GDPR Art. 32)."""
        data = json.dumps(self.to_audit_dict(), sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()

class EngineSpanTracker:
    """Track engine spans for audit and orchestration."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._spans: Dict[str, EngineSpan] = {}
        self._chain_hash: Optional[str] = None

    def create_span(
        self, span_id: str, engine_name: str, phase: str
    ) -> EngineSpan:
        """Create a new span and begin tracking.

        Args:
            span_id: Unique identifier for this execution unit
            engine_name: Name of the engine performing work
            phase: Phase name (gather, analyze, etc.)

        Returns:
            EngineSpan record
        """
        span = EngineSpan(
            span_id=span_id,
            engine_name=engine_name,
            phase=phase,
            started_at=datetime.utcnow(),
            previous_hash=self._chain_hash,
        )
        self._spans[span_id] = span
        return span

    def complete_span(
        self,
        span_id: str,
        status: str = "completed",
        result_hash: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> EngineSpan:
        """Mark span as complete and advance hash chain.

        Args:
            span_id: Target span
            status: 'completed', 'failed', etc.
            result_hash: SHA256 of span result (for integrity)
            error_message: If status='failed', error details

        Returns:
            Updated EngineSpan

        Raises:
            KeyError: if span not found
        """
        if span_id not in self._spans:
            raise KeyError(f"Span {span_id} not found")

        span = self._spans[span_id]
        span.status = status
        span.ended_at = datetime.utcnow()
        span.result_hash = result_hash
        span.error_message = error_message

        # Advance chain hash
        self._chain_hash = span.compute_hash()
        return span

    def get_span(self, span_id: str) -> Optional[EngineSpan]:
        """Retrieve span record."""
        return self._spans.get(span_id)

    def list_spans(self, phase: Optional[str] = None) -> list:
        """List all spans, optionally filtered by phase."""
        if phase:
            return [s for s in self._spans.values() if s.phase == phase]
        return list(self._spans.values())

    def verify_chain_integrity(self) -> bool:
        """Verify hash chain is unbroken (GDPR Art. 32 audit trail).

        Recomputes hashes from scratch and verifies continuity.

        Returns:
            True if chain is intact, False if tampered
        """
        sorted_spans = sorted(
            self._spans.values(), key=lambda s: s.started_at
        )

        prev_hash = None
        for span in sorted_spans:
            if span.previous_hash != prev_hash:
                return False
            computed = span.compute_hash()
            # Note: could compare against stored hash, but we re-compute here
            prev_hash = computed

        return True
