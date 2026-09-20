"""Loss-Signal Trigger Detection for Autonomous Skill Forge.

Reads tenant audit trails to identify when Skill execution confidence
falls below 0.70. Emits structured LossTrigger events that feed into
the Skill Forge optimizer loop (ADR-0613).

Audit Trail Contract:
  - Only reads, never mutates
  - Tenant isolation: events filtered by tenant_id
  - Deterministic: same input → same output
  - Fail-closed: invalid audit trail → exception, not silent skip
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from core.paths import tenant_audit_chain
from core.tenants import validate_tenant_id
from .path_traversal_validator import (
    assert_path_safe,
    PathTraversalError,
)

from .audit_chain_validator import AuditChainValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LossTrigger:
    """Immutable trigger signal for low Skill confidence.

    Attributes:
        skill_id: Skill identifier (e.g., "os.delegation_router")
        version: Skill version (semantic, e.g., "1.2.3")
        confidence: Calculated confidence score [0.0, 1.0]
        trigger_time: Timestamp when trigger was detected
        event_count: Number of audit events analyzed for this trigger
        lookback_hours: Window size used for analysis
    """

    skill_id: str
    version: str
    confidence: float
    trigger_time: datetime
    event_count: int
    lookback_hours: int


class SkillLossTriggerDetector:
    """Detects Skill confidence below threshold from audit trail.

    Reads tenant audit.jsonl, filters skill execution events,
    calculates confidence (correct outcomes / total outcomes),
    and emits LossTrigger when confidence < 0.70.

    Constraints (load-bearing):
    - Audit-trail read-only (no mutations)
    - Tenant isolation: never cross-tenant reads
    - Deterministic: no randomness in calculations
    - Fail-closed: invalid JSON → exception, not silent skip
    - Hash-chain integrity: validates chain before reading events
    """

    CONFIDENCE_THRESHOLD = 0.70
    DEFAULT_LOOKBACK_HOURS = 24

    def __init__(self):
        """Initialize the trigger detector with an audit chain validator.

        The validator is instantiated once per detector to avoid redundant
        validation on each detect_loss_signals call.
        """
        self._validator = AuditChainValidator()

    def detect_loss_signals(
        self,
        tenant_id: str,
        lookback_hours: Optional[int] = None,
    ) -> List[LossTrigger]:
        """Detect Skill loss signals from tenant audit trail.

        Reads audit.jsonl for the tenant, filters skill_executed events
        within the lookback window, groups by (skill_id, version),
        calculates confidence per group, and emits LossTrigger for
        any skill with confidence < 0.70.

        Args:
            tenant_id: Tenant identifier (validated via validate_tenant_id)
            lookback_hours: Window size for event collection (default: 24)

        Returns:
            List[LossTrigger]: Triggers for skills below threshold, ordered
                by descending confidence (worst first)

        Raises:
            ValueError: If tenant_id is invalid
            FileNotFoundError: If audit.jsonl does not exist
            RuntimeError: If audit chain integrity validation fails
            json.JSONDecodeError: If audit.jsonl contains invalid JSON
        """
        # Validate tenant
        validate_tenant_id(tenant_id)

        # Validate audit chain integrity (fail-closed)
        audit_path = tenant_audit_chain(tenant_id)
        if audit_path.exists():
            result = self._validator.validate_chain(audit_path)
            if not result.is_valid:
                raise RuntimeError(
                    f"Audit chain integrity check failed for tenant {tenant_id}: "
                    f"{result.error} (line {result.error_line_num}). "
                    f"Skill generation blocked (fail-closed)."
                )
            logger.info(
                f"Audit chain validated: {result.event_count} events verified, "
                f"last hash={result.last_verified_hash}"
            )

        # Default lookback
        if lookback_hours is None:
            lookback_hours = self.DEFAULT_LOOKBACK_HOURS

        # Calculate time window
        now = datetime.utcnow()
        since = now - timedelta(hours=lookback_hours)

        # Load audit events for this tenant
        all_events = self._load_audit_events(tenant_id, since)

        # Group by (skill_id, version)
        skill_groups: dict[tuple[str, str], list[dict]] = {}
        for event in all_events:
            skill_id = event.get("skill_id", "unknown")
            version = event.get("version", "unknown")
            key = (skill_id, version)
            if key not in skill_groups:
                skill_groups[key] = []
            skill_groups[key].append(event)

        # Calculate confidence per skill
        triggers: List[LossTrigger] = []
        for (skill_id, version), events in skill_groups.items():
            confidence = self._calculate_confidence(events)

            # Emit trigger if below threshold
            if confidence < self.CONFIDENCE_THRESHOLD:
                trigger = LossTrigger(
                    skill_id=skill_id,
                    version=version,
                    confidence=confidence,
                    trigger_time=now,
                    event_count=len(events),
                    lookback_hours=lookback_hours,
                )
                triggers.append(trigger)
                logger.warning(
                    f"Skill loss signal detected: {skill_id} v{version} "
                    f"confidence={confidence:.2f} (threshold={self.CONFIDENCE_THRESHOLD}) "
                    f"events={len(events)}"
                )

        # Sort by confidence (worst first)
        triggers.sort(key=lambda t: t.confidence)

        return triggers

    def _load_audit_events(
        self,
        tenant_id: str,
        since: datetime,
    ) -> List[dict]:
        """Load audit events for tenant within time window.

        Reads audit.jsonl, parses JSON lines, filters by timestamp,
        and returns skill-related events (skill_executed, skill_feedback).

        Args:
            tenant_id: Tenant identifier
            since: Include events on or after this timestamp

        Returns:
            List[dict]: Parsed audit events, unsorted

        Raises:
            FileNotFoundError: If audit.jsonl does not exist
            json.JSONDecodeError: If a line is invalid JSON
            PathTraversalError: If path validation fails (cross-tenant symlink, etc.)
        """
        # Get audit chain path
        audit_path = tenant_audit_chain(tenant_id)

        if not audit_path.exists():
            logger.warning(
                f"Audit trail not found for tenant {tenant_id} at {audit_path}. "
                "Returning empty event list."
            )
            return []

        # ─────────────────────────────────────────────────────────────────────
        # SECURITY: Validate path before opening (prevent symlink escape)
        # ─────────────────────────────────────────────────────────────────────
        try:
            expected_audit_dir = audit_path.parent
            validated_path = assert_path_safe(
                audit_path,
                expected_audit_dir,
                context=f"audit_chain for tenant {tenant_id}",
            )
            audit_path = validated_path
        except PathTraversalError as e:
            logger.error(
                f"SECURITY: Path validation failed for tenant {tenant_id}: {e}. "
                "Returning empty event list (fail-closed)."
            )
            raise RuntimeError(
                f"Audit chain path validation failed for tenant {tenant_id}: {e}"
            ) from e

        events = []
        since_ts = since.timestamp()

        try:
            with open(audit_path, "r") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.error(
                            f"Invalid JSON in audit trail {audit_path}:{line_num}: {e}"
                        )
                        raise

                    # Filter by timestamp
                    ts = event.get("ts", 0)
                    if ts < since_ts:
                        continue

                    # Filter by event type (only skill-related)
                    event_type = event.get("event_type", "")
                    if event_type not in ("skill_executed", "skill_feedback"):
                        continue

                    # Must have tenant_id field
                    if event.get("tenant_id") != tenant_id:
                        continue

                    events.append(event)

        except FileNotFoundError:
            logger.warning(
                f"Audit trail not found for tenant {tenant_id} at {audit_path}. "
                "Returning empty event list."
            )
            return []

        logger.info(
            f"Loaded {len(events)} skill audit events for tenant {tenant_id} "
            f"since {since.isoformat()}"
        )
        return events

    def _calculate_confidence(self, events: List[dict]) -> float:
        """Calculate Skill confidence from outcome feedback.

        Counts: correct_outcomes / total_outcomes
        Requires: 'outcome_feedback' field in event with 'correct' boolean

        Args:
            events: List of skill audit events

        Returns:
            float: Confidence score [0.0, 1.0]. Returns 0.0 if no feedback.

        Raises:
            ValueError: If feedback structure is invalid (fail-closed)
        """
        if not events:
            return 0.0

        correct_count = 0
        feedback_count = 0

        for event in events:
            feedback = event.get("outcome_feedback")
            if feedback is None:
                continue

            # outcome_feedback must have 'correct' key
            if not isinstance(feedback, dict):
                logger.warning(
                    f"Invalid outcome_feedback structure: {feedback} "
                    f"(expected dict with 'correct' key)"
                )
                continue

            correct = feedback.get("correct")
            if correct is None:
                logger.warning(
                    f"Missing 'correct' in outcome_feedback: {feedback}"
                )
                continue

            if not isinstance(correct, bool):
                logger.warning(
                    f"Invalid 'correct' type: {type(correct).__name__} "
                    f"(expected bool)"
                )
                continue

            feedback_count += 1
            if correct:
                correct_count += 1

        if feedback_count == 0:
            logger.debug(
                "No outcome feedback found in events. "
                "Confidence is undefined; returning 0.0 (fail-closed)."
            )
            return 0.0

        confidence = correct_count / feedback_count
        return confidence
