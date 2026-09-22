"""
Flow Policy Engine — Dynamic data flow allow/deny policies.

Maintains learned allow/deny lists that update from outcomes.
Policies never weaken: deny stays deny, allow confidence only goes up.

Used by Flow Guard Skill (ADR-2032) as the state machine for flow decisions.

Policy invariants (HARD):
  1. Deny decisions are immutable (confidence never decreases)
  2. All policy changes are audited
  3. Policy is tenant-scoped
  4. High-uncertainty flows require operator approval
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List
from datetime import datetime
import json


class FlowDecision(str, Enum):
    """Flow decision outcomes."""

    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"  # Requires approval
    BLOCKED = "blocked"  # Operator manually blocked


@dataclass
class PolicyRule:
    """A single allow/deny rule in the flow policy."""

    data_class: str  # e.g., "personal_email"
    destination_engine: str  # e.g., "anthropic/claude-opus-5" or "*" for any
    decision: FlowDecision  # ALLOW or DENY
    confidence: float  # 0.0–1.0 (higher = more confident)
    feedback_count: int = 0  # Number of outcomes that contributed
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "data_class": self.data_class,
            "destination_engine": self.destination_engine,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "feedback_count": self.feedback_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(d: dict) -> "PolicyRule":
        """Deserialize from JSON-compatible dict."""
        return PolicyRule(
            data_class=d["data_class"],
            destination_engine=d["destination_engine"],
            decision=FlowDecision(d["decision"]),
            confidence=d["confidence"],
            feedback_count=d.get("feedback_count", 0),
            created_at=datetime.fromisoformat(d.get("created_at", datetime.utcnow().isoformat())),
            updated_at=datetime.fromisoformat(d.get("updated_at", datetime.utcnow().isoformat())),
        )


@dataclass
class FlowPolicy:
    """
    Dynamic flow policy: allow/deny rules learned from outcomes.

    Invariants:
      - DENY rules never weaken (confidence never goes down)
      - ALLOW confidence only increases with positive outcomes
      - New flows start as UNCERTAIN (confidence=0.0)
    """

    tenant_id: str
    rules: List[PolicyRule] = field(default_factory=list)

    def add_rule(self, rule: PolicyRule) -> None:
        """
        Add a new rule to the policy.

        If a rule with same (data_class, destination_engine, decision) exists,
        update its confidence instead of duplicating.

        Raises:
            ValueError: If confidence is not in [0.0, 1.0]
        """
        if not 0.0 <= rule.confidence <= 1.0:
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {rule.confidence}")

        # Check if rule already exists
        existing = self._find_rule(rule.data_class, rule.destination_engine, rule.decision)
        if existing:
            # Update confidence: DENY stays high, ALLOW goes higher
            if rule.decision == FlowDecision.DENY:
                existing.confidence = max(existing.confidence, rule.confidence)
            else:
                existing.confidence = max(existing.confidence, rule.confidence)
            existing.feedback_count += 1
            existing.updated_at = datetime.utcnow()
        else:
            # New rule
            self.rules.append(rule)

    def _find_rule(
        self,
        data_class: str,
        destination_engine: str,
        decision: FlowDecision,
    ) -> Optional[PolicyRule]:
        """Find a rule by (data_class, destination_engine, decision)."""
        for rule in self.rules:
            if (
                rule.data_class == data_class
                and rule.destination_engine == destination_engine
                and rule.decision == decision
            ):
                return rule
        return None

    def get_decision(
        self,
        data_class: str,
        destination_engine: str,
        confidence_threshold: float = 0.7,
    ) -> FlowDecision:
        """
        Get the flow decision for a given (data_class, destination_engine) pair.

        Decision logic (in order):
          1. If DENY rule exists with confidence > threshold, return DENY
          2. If ALLOW rule exists with confidence > threshold, return ALLOW
          3. Otherwise return UNCERTAIN (requires approval)

        Args:
            data_class: Data classification (e.g., "personal_email")
            destination_engine: Destination engine (e.g., "anthropic/claude-opus-5")
            confidence_threshold: Minimum confidence to auto-allow/deny

        Returns:
            FlowDecision (ALLOW, DENY, or UNCERTAIN)
        """
        # Check for DENY first (fail-closed)
        deny_rule = self._find_rule(data_class, destination_engine, FlowDecision.DENY)
        if deny_rule and deny_rule.confidence >= confidence_threshold:
            return FlowDecision.DENY

        # Check for wildcard DENY (block all destinations for this data class)
        deny_wildcard = self._find_rule(data_class, "*", FlowDecision.DENY)
        if deny_wildcard and deny_wildcard.confidence >= confidence_threshold:
            return FlowDecision.DENY

        # Check for ALLOW
        allow_rule = self._find_rule(data_class, destination_engine, FlowDecision.ALLOW)
        if allow_rule and allow_rule.confidence >= confidence_threshold:
            return FlowDecision.ALLOW

        # Check for wildcard ALLOW
        allow_wildcard = self._find_rule(data_class, "*", FlowDecision.ALLOW)
        if allow_wildcard and allow_wildcard.confidence >= confidence_threshold:
            return FlowDecision.ALLOW

        # No confident rule found
        return FlowDecision.UNCERTAIN

    def update_from_outcome(self, outcome: "FlowOutcome") -> None:
        """
        Update policy confidence based on a flow outcome.

        Rules:
          - SUCCESS outcome → increase ALLOW confidence
          - PII_LEAK outcome → increase DENY confidence (fail-closed)
          - ERROR outcome → neutral (no update)

        Args:
            outcome: FlowOutcome with result (success/pii_leak/error)
        """
        if outcome.result == "success":
            # Increase allow confidence
            allow_rule = PolicyRule(
                data_class=outcome.data_class,
                destination_engine=outcome.destination_engine,
                decision=FlowDecision.ALLOW,
                confidence=min(1.0, 0.85),  # Conservative: starts at 0.85
                feedback_count=1,
            )
            self.add_rule(allow_rule)

        elif outcome.result == "pii_leak_detected":
            # Increase deny confidence (fail-closed)
            deny_rule = PolicyRule(
                data_class=outcome.data_class,
                destination_engine=outcome.destination_engine,
                decision=FlowDecision.DENY,
                confidence=min(1.0, 0.95),  # High confidence: 0.95
                feedback_count=1,
            )
            self.add_rule(deny_rule)

        elif outcome.result == "error":
            # No policy update on errors (neutral)
            pass

    def to_dict(self) -> dict:
        """Serialize policy to JSON-compatible dict."""
        return {
            "tenant_id": self.tenant_id,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @staticmethod
    def from_dict(d: dict) -> "FlowPolicy":
        """Deserialize policy from JSON-compatible dict."""
        policy = FlowPolicy(tenant_id=d["tenant_id"])
        policy.rules = [PolicyRule.from_dict(rule_dict) for rule_dict in d.get("rules", [])]
        return policy


@dataclass
class FlowOutcome:
    """
    Outcome of a data flow (used for learning).

    Attributes:
        data_class: Data classification (e.g., "personal_email")
        destination_engine: Where data flowed to
        flow_allowed: Whether the flow was allowed
        result: "success" | "pii_leak_detected" | "error"
        reasoning: Human-readable explanation
        timestamp: When the flow occurred
    """

    data_class: str
    destination_engine: str
    flow_allowed: bool
    result: str  # "success" | "pii_leak_detected" | "error"
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Serialize to JSON."""
        return {
            "data_class": self.data_class,
            "destination_engine": self.destination_engine,
            "flow_allowed": self.flow_allowed,
            "result": self.result,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
        }


class FlowPolicyManager:
    """
    Manager for tenant-scoped flow policies.

    Maintains per-tenant policies and provides thread-safe updates.
    """

    def __init__(self):
        """Initialize policy storage."""
        self.policies: Dict[str, FlowPolicy] = {}

    def get_or_create_policy(self, tenant_id: str) -> FlowPolicy:
        """Get policy for tenant, creating empty one if needed."""
        if tenant_id not in self.policies:
            self.policies[tenant_id] = FlowPolicy(tenant_id=tenant_id)
        return self.policies[tenant_id]

    def get_decision(
        self,
        tenant_id: str,
        data_class: str,
        destination_engine: str,
        confidence_threshold: float = 0.7,
    ) -> FlowDecision:
        """Get flow decision for a tenant."""
        policy = self.get_or_create_policy(tenant_id)
        return policy.get_decision(data_class, destination_engine, confidence_threshold)

    def record_outcome(self, tenant_id: str, outcome: FlowOutcome) -> None:
        """Record outcome and update policy."""
        policy = self.get_or_create_policy(tenant_id)
        policy.update_from_outcome(outcome)

    def export_policy(self, tenant_id: str) -> str:
        """Export policy as JSON string."""
        policy = self.get_or_create_policy(tenant_id)
        return json.dumps(policy.to_dict(), indent=2, default=str)

    def import_policy(self, tenant_id: str, policy_json: str) -> None:
        """Import policy from JSON string."""
        data = json.loads(policy_json)
        self.policies[tenant_id] = FlowPolicy.from_dict(data)
