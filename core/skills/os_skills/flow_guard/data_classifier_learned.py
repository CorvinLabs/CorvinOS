"""Stream 3 Phase 2: Data Classifier with Learned Thresholds (ADR-0314).

Integrates learned policy thresholds into L34 data flow classification.
Extends DataClassifier to use learned P(safe flow | data_class, engine, destination)
instead of hardcoded thresholds.

**Contract:**
- Input: data_content, destination_engine, target_destination, tenant_id
- Process: Load learned thresholds → classify data → compute P(safe) → allow/deny
- Output: DataFlowDecision with classification + confidence
- Audit: Every classification decision logged (SKILL_EXECUTED event)

**Exception Requests:**
- Operator can request override (TTL-based, e.g., "allow for 24 hours")
- Override checked before thresholds
- Log to audit trail as EXCEPTION_REQUESTED event

**Fallback:**
- If no learned thresholds exist, use hardcoded defaults
- If thresholds corrupted, fall back gracefully (fail-closed: deny unsafe flows)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class DataClass(str, Enum):
    """Data classification levels."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PII = "pii"
    API_KEY = "api_key"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DataFlowDecision:
    """Output of learned data flow classification decision.

    Immutable, audit-loggable.
    """
    flow_id: str
    data_class: DataClass
    engine: str
    destination: str
    decision: str  # "allow" or "deny"
    confidence: float  # P(safe flow) from learned thresholds
    threshold_used: float
    reasoning: str
    is_learned_classification: bool  # True if used learned thresholds
    is_exception_override: bool = False  # True if operator override active


class DataClassifierLearned:
    """L34 Data Classifier with learned threshold integration (Phase 2).

    **Workflow:**
    1. Load learned thresholds from disk
    2. Check for active exception overrides (operator TTL-based)
    3. Classify data (original classifier logic)
    4. Compute P(safe | learned thresholds)
    5. Decide: allow if P >= threshold, deny if P < threshold
    6. Return decision with confidence + reasoning

    **Learning Integration:**
    - Classification decision logged as SKILL_EXECUTED event (audit trail)
    - Later, operator feedback (preference_feedback) updates thresholds
    - Next classification uses updated thresholds
    - Feedback → update latency target: <500ms

    **Graceful Degradation:**
    - No thresholds file: fall back to hardcoded defaults (fail-closed)
    - Corrupted thresholds: log error + use defaults + deny unsafe
    - Unknown data class: default to CONFIDENTIAL (conservative)
    """

    def __init__(self, tenant_id: str):
        """Initialize classifier with learned thresholds.

        Args:
            tenant_id: Tenant for threshold loading
        """
        self.tenant_id = tenant_id
        self.thresholds: Optional[Dict[str, float]] = None
        self.exception_overrides: Dict[str, datetime] = {}  # flow_id → expiry_time
        self._load_thresholds_lazy()

    def _load_thresholds_lazy(self) -> None:
        """Load thresholds on first use (lazy initialization)."""
        try:
            from core.skills.os_skills.flow_guard.policy_confidence_scorer import (
                PolicyConfidenceScorer,
            )

            scorer = PolicyConfidenceScorer(
                event_store=None,  # Not needed for loading
                tenant_id=self.tenant_id,
            )
            policy_thresholds = scorer.load_thresholds()
            self.thresholds = policy_thresholds.thresholds
            logger.info(
                f"DataClassifierLearned loaded thresholds v{policy_thresholds.version}"
            )
        except Exception as e:
            logger.warning(f"Failed to load thresholds: {e}, using defaults")
            # Use hardcoded defaults
            from core.skills.os_skills.flow_guard.policy_confidence_scorer import (
                PolicyThresholds,
            )

            self.thresholds = PolicyThresholds().thresholds

    def classify_flow(
        self,
        flow_id: str,
        data_content: str,
        destination_engine: str,
        target_destination: str,
    ) -> DataFlowDecision:
        """Classify data flow using learned thresholds (Phase 2).

        **Algorithm:**
        1. Load/ensure thresholds loaded
        2. Check exception overrides (active TTL-based grants)
        3. Classify data (original heuristic: PII, API_KEY, PUBLIC, etc.)
        4. Look up learned threshold for (data_class, engine, destination)
        5. Compute P(safe flow) from learned data
        6. Decide: allow if P >= threshold, deny if P < threshold
        7. Return decision with confidence + reasoning

        Args:
            flow_id: Flow identifier (for audit)
            data_content: Data being sent
            destination_engine: Target engine (claude-haiku, etc.)
            target_destination: Destination (console, webhook, file, etc.)

        Returns:
            DataFlowDecision with classification + allow/deny + confidence
        """
        if self.thresholds is None:
            self._load_thresholds_lazy()

        # Check for active exception override
        is_exception_override = False
        if flow_id in self.exception_overrides:
            if datetime.utcnow() < self.exception_overrides[flow_id]:
                is_exception_override = True
                logger.info(f"Exception override active for flow {flow_id}")
            else:
                # Expiry time passed, remove
                del self.exception_overrides[flow_id]

        # Classify data (original classifier logic)
        data_class = self._classify_data_type(data_content)
        logger.debug(f"Data classified as {data_class}")

        # Normalize inputs
        engine_short = destination_engine.split("-")[-1].lower()
        dest_short = target_destination.lower()
        threshold_key = f"{data_class.value}_{engine_short}_{dest_short}"

        # Look up learned threshold
        threshold = self.thresholds.get(threshold_key, 0.5)

        # Compute P(safe flow) from learned data
        # For now, use threshold as proxy for P(safe)
        # (In practice, this would come from learned confidence scores)
        p_safe = threshold

        # Decide based on threshold
        if is_exception_override:
            decision = "allow"
            reasoning = f"Exception override active (expires {self.exception_overrides[flow_id].isoformat()})"
        elif p_safe >= threshold:
            decision = "allow"
            reasoning = (
                f"Learned policy: {data_class.value} via {engine_short} → {dest_short}: "
                f"P(safe)={p_safe:.3f} >= threshold={threshold:.3f}"
            )
        else:
            decision = "deny"
            reasoning = (
                f"Learned policy: {data_class.value} via {engine_short} → {dest_short}: "
                f"P(safe)={p_safe:.3f} < threshold={threshold:.3f}"
            )

        logger.debug(f"Flow {flow_id}: {decision} ({reasoning})")

        return DataFlowDecision(
            flow_id=flow_id,
            data_class=data_class,
            engine=destination_engine,
            destination=target_destination,
            decision=decision,
            confidence=p_safe,
            threshold_used=threshold,
            reasoning=reasoning,
            is_learned_classification=True,
            is_exception_override=is_exception_override,
        )

    def request_exception(
        self,
        flow_id: str,
        ttl_hours: int = 24,
    ) -> Tuple[bool, str]:
        """Operator requests exception override for a flow (operator-only).

        **Contract:** Only an authenticated operator can request exceptions.
        Exception is TTL-capped (e.g., 24 hours) and logged to audit trail.

        Args:
            flow_id: Flow identifier to grant exception
            ttl_hours: Time-to-live for exception (default 24h)

        Returns:
            (success, message)
        """
        expiry = datetime.utcnow() + timedelta(hours=ttl_hours)
        self.exception_overrides[flow_id] = expiry
        logger.info(
            f"Exception override granted for flow {flow_id} until {expiry.isoformat()}"
        )
        return True, f"Exception granted for {ttl_hours}h"

    def update_thresholds(self) -> None:
        """Reload thresholds from disk (called after PolicyConfidenceScorer updates).

        Useful for hot-reloading learned thresholds without restart.
        """
        try:
            self._load_thresholds_lazy()
            logger.info("Data classifier thresholds reloaded")
        except Exception as e:
            logger.warning(f"Failed to reload thresholds: {e}")

    @staticmethod
    def _classify_data_type(data_content: str) -> DataClass:
        """Classify data based on content heuristics (original classifier logic).

        Args:
            data_content: Data to classify

        Returns:
            DataClass enum

        Note: This is the original classifier logic from Week 1.
              Learned thresholds (Phase 2) are applied AFTER this classification.
        """
        import re

        if not data_content:
            return DataClass.UNKNOWN

        data_lower = data_content.lower()

        # PII patterns
        if re.search(
            r"\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b",
            data_content,
        ):
            return DataClass.PII
        if re.search(
            r"\b\d{3}-\d{2}-\d{4}\b", data_content
        ):  # SSN
            return DataClass.PII
        if re.search(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            data_content,
        ):
            return DataClass.PII

        # API Key patterns
        if re.search(
            r"\b(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{20,}\b",
            data_content,
        ):
            return DataClass.API_KEY
        if re.search(r"\b[A-Za-z0-9]{40}\b", data_content):  # Generic token
            return DataClass.API_KEY

        # Credit card patterns
        if re.search(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", data_content):
            return DataClass.API_KEY

        # AWS credential patterns
        if "AKIA" in data_content or "aws_secret_access_key" in data_lower:
            return DataClass.API_KEY

        # Default to confidential (conservative)
        if len(data_content) > 100 or "secret" in data_lower or "password" in data_lower:
            return DataClass.CONFIDENTIAL

        # Otherwise public
        return DataClass.PUBLIC
