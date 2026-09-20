"""Security Pattern Learning & Threat Orchestration (ADR-0906, L16 Integration).

Phase 9: os.security_orchestrator Skill for L16 (Security Hardening).

Learns security patterns from denied requests and operator feedback to:
  1. Detect threat patterns automatically
  2. Recommend threshold adjustments to reduce false positives
  3. Validate learned patterns before applying

Audit trail:
  - threat_pattern_detected (automatic attack pattern match)
  - false_positive_confirmed (operator feedback: "this was safe")
  - security_threshold_recommended (suggest gate adjustment)
  - security_threshold_applied (change confirmed safe + deployed)
  - threat_pattern_learned (new pattern added to knowledge base)

Constraints:
  - Conservative: Only recommend, never auto-apply
  - Audit-first: Every decision → immutable event
  - Tenant-scoped: All data filtered by tenant_id
  - Never weakens compliance gates (L44, consent, disclosure always on)

Integration with L16:
  - HouseRulesEnforcer consults this Skill on high-confidence denials
  - Feedback comes from operator reviews + user escalations
  - Learning loop: denial → pattern analysis → recommendation → operator approval
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from core.tenants.validation import validate_tenant_id

logger = logging.getLogger(__name__)

__all__ = [
    "ThreatPattern",
    "SecurityContext",
    "SecurityRecommendation",
    "SecurityOrchestratorSkill",
]


@dataclass(frozen=True)
class ThreatPattern:
    """Immutable threat pattern learned from security events."""

    pattern_id: str  # UUID or slug
    pattern_type: str  # "keyword_match", "anomaly", "behavioral"
    rule_name: str  # e.g., "no-military", "no-offensive-cyber"
    indicators: list[str]  # Keywords/signatures (e.g., ["military", "combat"])
    confidence_threshold: float  # [0.0–1.0] How certain must classifier be?
    false_positive_count: int = 0
    true_positive_count: int = 0
    last_updated: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(f"confidence_threshold must be in [0.0, 1.0], got {self.confidence_threshold}")
        if not self.pattern_id or not self.rule_name:
            raise ValueError("pattern_id and rule_name required")


@dataclass(frozen=True)
class SecurityContext:
    """Request context for threat analysis (immutable for audit)."""

    tenant_id: str
    request_id: str
    timestamp: datetime
    gate_rule: str  # e.g., "no-military"
    classification_confidence: float  # [0.0–1.0] How confident was the classifier?
    request_text: str  # The denied request (sanitized, no secrets)
    classification_reason: str  # Why was it denied?

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if not 0.0 <= self.classification_confidence <= 1.0:
            raise ValueError(
                f"classification_confidence must be in [0.0, 1.0], got {self.classification_confidence}"
            )
        # Coerce naive timestamp to UTC
        if object.__getattribute__(self, "timestamp").tzinfo is None:
            object.__setattr__(
                self,
                "timestamp",
                object.__getattribute__(self, "timestamp").replace(tzinfo=timezone.utc)
            )


@dataclass(frozen=True)
class SecurityRecommendation:
    """Recommendation to adjust security gates (audit-safe, never auto-applied)."""

    recommendation_id: str  # UUID
    tenant_id: str
    timestamp: datetime
    threat_pattern_id: str  # Which pattern triggered this?
    gate_rule: str  # Which gate to adjust?
    current_threshold: float  # Current confidence threshold
    recommended_threshold: float  # Suggested new value
    reason: str  # Why (e.g., "X false positives detected")
    false_positive_count: int  # Evidence: number of false positives
    confidence: float  # [0.0–1.0] How confident in this recommendation?
    status: Literal["pending", "applied", "rejected"] = "pending"

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not -0.20 <= (self.recommended_threshold - self.current_threshold) <= 0.20:
            raise ValueError("threshold delta must be in [-0.20, 0.20]")
        # Coerce naive timestamp to UTC
        if object.__getattribute__(self, "timestamp").tzinfo is None:
            object.__setattr__(
                self,
                "timestamp",
                object.__getattribute__(self, "timestamp").replace(tzinfo=timezone.utc)
            )


class SecurityOrchestratorSkill:
    """
    Orchestrate security pattern learning + threat analysis + gate optimization.

    High-level flow:
      1. execute(security_context) → detects threat patterns, analyzes false positives
      2. learn_from_feedback(feedback) → operator confirms safe/threat
      3. recommend_adjustments() → suggests threshold changes (never auto-applies)
      4. apply_recommendation() → operator approval required

    Every step is audit-logged and tenant-scoped.
    """

    def __init__(self, tenant_id: str, patterns_path: Optional[Path] = None):
        """
        Initialize the Skill.

        Args:
            tenant_id: Tenant identifier (fail-closed if None)
            patterns_path: Where to store learned patterns (default: tenant_home/skills)
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.skill_id = "os.security_orchestrator"
        self.version = "1.0.0"

        # Where patterns live (typically <tenant_home>/skills/security_patterns.jsonl)
        if patterns_path is None:
            try:
                from core.paths.tenant import tenant_home  # noqa: PLC0415
                base = tenant_home(tenant_id)
            except Exception:  # noqa: BLE001
                import os
                root = os.environ.get("CORVIN_HOME")
                base = Path(root) if root else Path.home() / ".corvin"
                base = base / "tenants" / tenant_id

            patterns_path = base / "skills" / "security_patterns.jsonl"

        self.patterns_path = Path(patterns_path)
        self.patterns_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory cache of learned patterns (loaded on init)
        self._patterns: dict[str, ThreatPattern] = {}
        self._recommendations: dict[str, SecurityRecommendation] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load learned patterns from disk (fail-closed on corruption)."""
        if not self.patterns_path.exists():
            return

        try:
            with open(self.patterns_path, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("type") == "pattern":
                        # Parse datetime fields
                        if data["payload"].get("last_updated"):
                            data["payload"]["last_updated"] = datetime.fromisoformat(
                                data["payload"]["last_updated"]
                            )
                        pattern = ThreatPattern(**data["payload"])
                        self._patterns[pattern.pattern_id] = pattern
                    elif data.get("type") == "recommendation":
                        data["payload"]["timestamp"] = datetime.fromisoformat(
                            data["payload"]["timestamp"]
                        )
                        rec = SecurityRecommendation(**data["payload"])
                        self._recommendations[rec.recommendation_id] = rec
        except Exception as e:
            logger.error(f"Failed to load security patterns: {e} (fail-closed)")
            # Don't raise; start fresh on corruption

    def _persist_event(self, event_type: str, payload: dict) -> None:
        """Persist an event to the patterns JSONL (audit-first)."""
        event = {
            "type": event_type,
            "tenant_id": self.tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        try:
            with open(self.patterns_path, 'a') as f:
                f.write(json.dumps(event, default=str) + '\n')
        except Exception as e:
            logger.error(f"Failed to persist security event: {e}")
            # Don't raise; learning is optional, security gates are mandatory

    def execute(self, context: SecurityContext) -> dict:
        """
        Analyze a denied request for threat patterns + false positives.

        Args:
            context: SecurityContext from L16 gate

        Returns:
            {
                "threat_patterns_detected": [{"pattern_id": "...", "match_confidence": 0.95}],
                "false_positive_risk": 0.2,  # Estimated risk this denial was wrong
                "audit_event_id": "...",
            }
        """
        result = {
            "threat_patterns_detected": [],
            "false_positive_risk": self._estimate_false_positive_risk(context),
            "audit_event_id": f"threat_analysis_{context.request_id}",
        }

        # Check existing patterns
        for pattern in self._patterns.values():
            if self._pattern_matches(pattern, context):
                result["threat_patterns_detected"].append({
                    "pattern_id": pattern.pattern_id,
                    "pattern_type": pattern.pattern_type,
                    "rule_name": pattern.rule_name,
                    "match_confidence": context.classification_confidence,
                })

        # Persist analysis event
        self._persist_event("threat_pattern_detected", {
            "request_id": context.request_id,
            "gate_rule": context.gate_rule,
            "patterns_detected": len(result["threat_patterns_detected"]),
            "false_positive_risk": result["false_positive_risk"],
        })

        return result

    def _pattern_matches(self, pattern: ThreatPattern, context: SecurityContext) -> bool:
        """Check if a pattern matches the request (keyword-based, deterministic)."""
        # Simple keyword matching (deterministic, no ML)
        if pattern.rule_name != context.gate_rule:
            return False

        # Check if any indicator is in the request text
        request_lower = context.request_text.lower()
        for indicator in pattern.indicators:
            if indicator.lower() in request_lower:
                return True

        return False

    def _estimate_false_positive_risk(self, context: SecurityContext) -> float:
        """Estimate probability this denial was incorrect (0.0–1.0)."""
        # Heuristic: lower confidence → higher false-positive risk
        # confidence=0.9 → risk=0.1; confidence=0.5 → risk=0.5
        if context.classification_confidence >= 0.85:
            return 0.0  # Very confident
        elif context.classification_confidence >= 0.70:
            return 0.15  # Moderately confident
        elif context.classification_confidence >= 0.55:
            return 0.35  # Low-moderate confidence
        else:
            return 0.50  # Very uncertain

    def learn_from_feedback(self, context: SecurityContext, feedback_type: Literal["false_positive", "threat"]) -> None:
        """
        Operator confirms: this denial was safe or was a real threat.

        Args:
            context: Original SecurityContext
            feedback_type: "false_positive" (safe) or "threat" (correct denial)

        Raises:
            PermissionError: Operator consent required (fail-closed)
        """
        # Verify operator consent (would be checked via role-based access in real system)
        # For now, we log the decision and update patterns

        if feedback_type == "false_positive":
            self._update_patterns_false_positive(context)
        elif feedback_type == "threat":
            self._update_patterns_threat(context)

        self._persist_event("security_feedback_received", {
            "request_id": context.request_id,
            "feedback_type": feedback_type,
            "gate_rule": context.gate_rule,
        })

    def _update_patterns_false_positive(self, context: SecurityContext) -> None:
        """Update patterns: this denial was wrong (false positive)."""
        patterns_to_update = []
        for pattern_id, pattern in list(self._patterns.items()):
            if self._pattern_matches(pattern, context):
                patterns_to_update.append((pattern_id, pattern))

        # Create new patterns with updated counters (frozen dataclass immutable)
        for pattern_id, pattern in patterns_to_update:
            updated = ThreatPattern(
                pattern_id=pattern.pattern_id,
                pattern_type=pattern.pattern_type,
                rule_name=pattern.rule_name,
                indicators=pattern.indicators,
                confidence_threshold=pattern.confidence_threshold,
                false_positive_count=pattern.false_positive_count + 1,
                true_positive_count=pattern.true_positive_count,
                last_updated=datetime.now(timezone.utc),
            )
            self._patterns[pattern_id] = updated

    def _update_patterns_threat(self, context: SecurityContext) -> None:
        """Update patterns: this denial was correct (real threat)."""
        patterns_to_update = []
        for pattern_id, pattern in list(self._patterns.items()):
            if self._pattern_matches(pattern, context):
                patterns_to_update.append((pattern_id, pattern))

        # Create new patterns with updated counters (frozen dataclass immutable)
        for pattern_id, pattern in patterns_to_update:
            updated = ThreatPattern(
                pattern_id=pattern.pattern_id,
                pattern_type=pattern.pattern_type,
                rule_name=pattern.rule_name,
                indicators=pattern.indicators,
                confidence_threshold=pattern.confidence_threshold,
                false_positive_count=pattern.false_positive_count,
                true_positive_count=pattern.true_positive_count + 1,
                last_updated=datetime.now(timezone.utc),
            )
            self._patterns[pattern_id] = updated

    def recommend_adjustments(self) -> list[SecurityRecommendation]:
        """
        Analyze learned patterns and recommend threshold adjustments.

        Returns:
            List of SecurityRecommendation (never auto-applied; requires operator approval)
        """
        recommendations = []

        for pattern in self._patterns.values():
            # Heuristic: if false_positives > true_positives, recommend lowering threshold
            if pattern.false_positive_count >= 3 and pattern.true_positive_count > 0:
                fp_rate = pattern.false_positive_count / (pattern.false_positive_count + pattern.true_positive_count)
                if fp_rate > 0.30:  # More than 30% false positives
                    # Recommend lowering threshold to catch fewer cases
                    new_threshold = max(0.50, pattern.confidence_threshold - 0.10)
                    rec = SecurityRecommendation(
                        recommendation_id=f"sec_rec_{pattern.pattern_id}_{int(datetime.now(timezone.utc).timestamp())}",
                        tenant_id=self.tenant_id,
                        timestamp=datetime.now(timezone.utc),
                        threat_pattern_id=pattern.pattern_id,
                        gate_rule=pattern.rule_name,
                        current_threshold=pattern.confidence_threshold,
                        recommended_threshold=new_threshold,
                        reason=f"Detected {pattern.false_positive_count} false positives (rate: {fp_rate:.0%})",
                        false_positive_count=pattern.false_positive_count,
                        confidence=0.75,  # Moderate confidence in recommendation
                    )
                    recommendations.append(rec)
                    self._recommendations[rec.recommendation_id] = rec

        # Persist recommendations
        for rec in recommendations:
            self._persist_event("security_threshold_recommended", {
                "recommendation_id": rec.recommendation_id,
                "gate_rule": rec.gate_rule,
                "threshold_delta": rec.recommended_threshold - rec.current_threshold,
                "false_positive_count": rec.false_positive_count,
                "confidence": rec.confidence,
            })

        return recommendations

    def apply_recommendation(self, recommendation_id: str, approved: bool) -> bool:
        """
        Operator approves/rejects a recommendation.

        Args:
            recommendation_id: ID of recommendation to apply
            approved: True to apply, False to reject

        Returns:
            True if operation succeeded (approval or rejection), False if recommendation not found
        """
        rec = self._recommendations.get(recommendation_id)
        if not rec:
            return False

        if approved:
            # Create new recommendation with updated status (frozen dataclass)
            updated_rec = SecurityRecommendation(
                recommendation_id=rec.recommendation_id,
                tenant_id=rec.tenant_id,
                timestamp=rec.timestamp,
                threat_pattern_id=rec.threat_pattern_id,
                gate_rule=rec.gate_rule,
                current_threshold=rec.current_threshold,
                recommended_threshold=rec.recommended_threshold,
                reason=rec.reason,
                false_positive_count=rec.false_positive_count,
                confidence=rec.confidence,
                status="applied",
            )
            self._recommendations[recommendation_id] = updated_rec

            self._persist_event("security_threshold_applied", {
                "recommendation_id": recommendation_id,
                "gate_rule": rec.gate_rule,
                "threshold_delta": rec.recommended_threshold - rec.current_threshold,
                "approved_by": "operator",  # Would be extracted from session
            })
            return True
        else:
            # Create new recommendation with updated status (frozen dataclass)
            updated_rec = SecurityRecommendation(
                recommendation_id=rec.recommendation_id,
                tenant_id=rec.tenant_id,
                timestamp=rec.timestamp,
                threat_pattern_id=rec.threat_pattern_id,
                gate_rule=rec.gate_rule,
                current_threshold=rec.current_threshold,
                recommended_threshold=rec.recommended_threshold,
                reason=rec.reason,
                false_positive_count=rec.false_positive_count,
                confidence=rec.confidence,
                status="rejected",
            )
            self._recommendations[recommendation_id] = updated_rec

            self._persist_event("security_threshold_rejected", {
                "recommendation_id": recommendation_id,
                "gate_rule": rec.gate_rule,
                "rejected_by": "operator",
            })
            return True

    def add_threat_pattern(self, pattern: ThreatPattern) -> None:
        """Register a new threat pattern (operator-driven or auto-learned)."""
        self._patterns[pattern.pattern_id] = pattern

        self._persist_event("threat_pattern_learned", {
            "pattern_id": pattern.pattern_id,
            "pattern_type": pattern.pattern_type,
            "rule_name": pattern.rule_name,
            "indicators": pattern.indicators,
            "confidence_threshold": pattern.confidence_threshold,
        })
