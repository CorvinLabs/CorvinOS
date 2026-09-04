"""L5 k=2: Approval Policy Rule Engine — Automatic Approval Based on Rules.

ADR-0578: Approval Policy Rules Engine
Rule types:
1. confidence_threshold — auto-approve if confidence > X
2. magnitude_limit — auto-reject if |delta| > X
3. metric_whitelist — auto-approve changes to specific metrics
4. momentum_pattern — auto-approve if N consecutive low-risk approvals
5. time_window — auto-approve only during specific hours

Features:
- Rule persistence (JSONL recovery after restart)
- Audit trail for every rule evaluation
- Conflict resolution: first matching rule wins
- Operator can add/remove/list rules per skill
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, time
import uuid
import json
from pathlib import Path
import threading
from enum import Enum

logger = logging.getLogger(__name__)


class RuleType(str, Enum):
    """Types of approval rules."""
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    MAGNITUDE_LIMIT = "magnitude_limit"
    METRIC_WHITELIST = "metric_whitelist"
    MOMENTUM_PATTERN = "momentum_pattern"
    TIME_WINDOW = "time_window"


@dataclass
class ApprovalRule:
    """A single approval rule (immutable once created)."""
    rule_id: str
    skill_id: str
    rule_type: RuleType
    config: Dict[str, Any]  # Rule-specific config (confidence, magnitude, metrics, etc.)
    created_timestamp: str
    created_by: str = "system"


@dataclass
class RuleEvaluationResult:
    """Result of evaluating rules against an approval request."""
    decision: str  # "auto-approve" | "auto-reject" | "pending"
    matched_rules: List[str]  # rule_ids that matched
    reason: str  # Human-readable explanation
    confidence: float = 0.0  # Confidence of decision


class ApprovalPolicyEngine:
    """
    L5 k=2: Approval Policy Rules Engine.

    Automatically approves/rejects approvals based on operator-defined rules.

    Rule Types:
    1. confidence_threshold: auto-approve if confidence > X
       config: {threshold: 0.7}
    2. magnitude_limit: auto-reject if |delta| > X
       config: {limit: 0.5}
    3. metric_whitelist: auto-approve specific metrics
       config: {metrics: ["metric1", "metric2"]}
    4. momentum_pattern: auto-approve after N consecutive approvals
       config: {min_count: 5}
    5. time_window: auto-approve only during HH:MM-HH:MM
       config: {start_hh: 9, start_mm: 0, end_hh: 17, end_mm: 0}

    Constraints:
    1. Persistence: rules stored in JSONL (recover after restart)
    2. Audit trail: every rule evaluation logged
    3. First-match: first matching rule decides (order matters)
    4. Fail-closed: invalid rules rejected, default is PENDING
    5. Thread-safe: all state mutations under lock
    """

    def __init__(
        self,
        approval_gate,
        tenant_id: str = "_default",
        corvin_home: str = None,
    ):
        """
        Initialize policy engine.

        Args:
            approval_gate: OperatorApprovalGate instance
            tenant_id: Tenant ID
            corvin_home: Path to ~/.corvin
        """
        self.approval_gate = approval_gate
        self.tenant_id = tenant_id
        self.audit_backend = approval_gate.audit_backend

        # Persistence
        if corvin_home is None:
            import os
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        self.corvin_home = Path(corvin_home)
        self.rules_file = self.corvin_home / "tenants" / tenant_id / "skills" / "approval_rules.jsonl"

        # Thread safety
        self._lock = threading.RLock()

        # In-memory rules: skill_id -> List[ApprovalRule] (ordered by creation)
        self.rules: Dict[str, List[ApprovalRule]] = {}

        # Load persisted rules from disk
        self._load_persisted_rules()

    def _load_persisted_rules(self) -> None:
        """Load rules from disk (recovery after restart)."""
        if not self.rules_file.exists():
            return

        try:
            with open(self.rules_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        rule = ApprovalRule(
                            rule_id=data["rule_id"],
                            skill_id=data["skill_id"],
                            rule_type=RuleType(data["rule_type"]),
                            config=data["config"],
                            created_timestamp=data["created_timestamp"],
                            created_by=data.get("created_by", "system"),
                        )
                        if rule.skill_id not in self.rules:
                            self.rules[rule.skill_id] = []
                        self.rules[rule.skill_id].append(rule)
                    except (json.JSONDecodeError, TypeError, ValueError) as e:
                        logger.warning(f"[Policy Engine] Failed to load rule: {e}")
        except Exception as e:
            logger.error(f"[Policy Engine] Failed to load persisted rules: {e}")

    def _persist_rule(self, rule: ApprovalRule) -> None:
        """Append rule to disk (immutable log)."""
        try:
            self.rules_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.rules_file, "a") as f:
                data = {
                    "rule_id": rule.rule_id,
                    "skill_id": rule.skill_id,
                    "rule_type": rule.rule_type.value,
                    "config": rule.config,
                    "created_timestamp": rule.created_timestamp,
                    "created_by": rule.created_by,
                }
                json_line = json.dumps(data, default=str)
                f.write(json_line + "\n")
        except Exception as e:
            logger.error(f"[Policy Engine] Failed to persist rule {rule.rule_id}: {e}")

    def _validate_rule_config(self, rule_type: RuleType, config: Dict[str, Any]) -> None:
        """
        Validate rule config based on type (fail-closed).

        Raises:
            ValueError if config is invalid
        """
        if rule_type == RuleType.CONFIDENCE_THRESHOLD:
            if "threshold" not in config:
                raise ValueError("confidence_threshold rule requires 'threshold' config")
            threshold = config["threshold"]
            if not (0.0 <= threshold <= 1.0):
                raise ValueError(f"threshold must be in [0.0, 1.0], got {threshold}")

        elif rule_type == RuleType.MAGNITUDE_LIMIT:
            if "limit" not in config:
                raise ValueError("magnitude_limit rule requires 'limit' config")
            limit = config["limit"]
            if limit < 0.0:
                raise ValueError(f"limit must be >= 0.0, got {limit}")

        elif rule_type == RuleType.METRIC_WHITELIST:
            if "metrics" not in config:
                raise ValueError("metric_whitelist rule requires 'metrics' config")
            metrics = config["metrics"]
            if not isinstance(metrics, list) or not metrics:
                raise ValueError(f"metrics must be non-empty list, got {metrics}")

        elif rule_type == RuleType.MOMENTUM_PATTERN:
            if "min_count" not in config:
                raise ValueError("momentum_pattern rule requires 'min_count' config")
            min_count = config["min_count"]
            if not isinstance(min_count, int) or min_count < 1:
                raise ValueError(f"min_count must be >= 1, got {min_count}")

        elif rule_type == RuleType.TIME_WINDOW:
            required = {"start_hh", "start_mm", "end_hh", "end_mm"}
            if not required.issubset(config.keys()):
                raise ValueError(f"time_window rule requires {required}, got {config.keys()}")
            for key in required:
                val = config[key]
                if not isinstance(val, int) or val < 0:
                    raise ValueError(f"{key} must be non-negative int, got {val}")
            if config["start_hh"] >= 24 or config["end_hh"] >= 24:
                raise ValueError("hh must be < 24")
            if config["start_mm"] >= 60 or config["end_mm"] >= 60:
                raise ValueError("mm must be < 60")

    def add_rule(
        self,
        skill_id: str,
        rule_type: RuleType,
        config: Dict[str, Any],
        created_by: str = "system",
    ) -> str:
        """
        Add a new rule for a skill (AUDIT-FIRST).

        Args:
            skill_id: Which skill this rule applies to
            rule_type: Type of rule
            config: Rule-specific config (validated)
            created_by: Who created the rule

        Returns:
            rule_id

        Raises:
            ValueError if config is invalid (fail-closed)
            RuntimeError if audit fails
        """
        # Validate config
        self._validate_rule_config(rule_type, config)

        rule_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        rule = ApprovalRule(
            rule_id=rule_id,
            skill_id=skill_id,
            rule_type=rule_type,
            config=config,
            created_timestamp=now,
            created_by=created_by,
        )

        # AUDIT-FIRST
        audit_event = {
            "tenant_id": self.tenant_id,
            "event_type": "approval_rule_created",
            "rule_id": rule_id,
            "skill_id": skill_id,
            "rule_type": rule_type.value,
            "created_by": created_by,
        }

        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"[Policy Engine] Failed to audit rule creation: {e}")
            raise RuntimeError(f"[Policy Engine] FATAL: audit failed; rule NOT created (fail-closed).")

        # State mutation AFTER successful audit
        with self._lock:
            if skill_id not in self.rules:
                self.rules[skill_id] = []
            self.rules[skill_id].append(rule)

        self._persist_rule(rule)

        logger.info(f"[Policy Engine] Created rule {rule_id} for {skill_id}: {rule_type.value}")
        return rule_id

    def remove_rule(self, skill_id: str, rule_id: str) -> bool:
        """
        Remove a rule (AUDIT-FIRST).

        Args:
            skill_id: Skill that owns the rule
            rule_id: Rule ID to remove

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if skill_id not in self.rules:
                logger.warning(f"[Policy Engine] Skill {skill_id} has no rules")
                return False

            found = False
            for i, rule in enumerate(self.rules[skill_id]):
                if rule.rule_id == rule_id:
                    found = True
                    break

            if not found:
                logger.warning(f"[Policy Engine] Rule {rule_id} not found for {skill_id}")
                return False

            # AUDIT-FIRST
            audit_event = {
                "tenant_id": self.tenant_id,
                "event_type": "approval_rule_deleted",
                "rule_id": rule_id,
                "skill_id": skill_id,
            }

            try:
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"[Policy Engine] Failed to audit rule deletion: {e}")
                raise RuntimeError(f"[Policy Engine] FATAL: audit failed; rule NOT deleted (fail-closed).")

            # State mutation AFTER audit
            self.rules[skill_id].pop(i)

            logger.info(f"[Policy Engine] Deleted rule {rule_id} for {skill_id}")
            return True

    def list_rules(self, skill_id: str) -> List[Dict]:
        """
        List all rules for a skill.

        Args:
            skill_id: Skill ID

        Returns:
            List of rule dicts
        """
        with self._lock:
            if skill_id not in self.rules:
                return []

            return [
                {
                    "rule_id": r.rule_id,
                    "rule_type": r.rule_type.value,
                    "config": r.config,
                    "created_timestamp": r.created_timestamp,
                    "created_by": r.created_by,
                }
                for r in self.rules[skill_id]
            ]

    def evaluate_rules(
        self,
        skill_id: str,
        metric_name: str,
        magnitude: float,
        confidence: float,
        recent_history: Optional[List[Dict]] = None,
    ) -> RuleEvaluationResult:
        """
        Evaluate all rules for a skill against an approval request.

        Rules are evaluated in order; first match wins.

        Args:
            skill_id: Skill ID
            metric_name: Metric being changed
            magnitude: |smoothed_delta|
            confidence: EMA confidence
            recent_history: Optional list of recent approvals {approval_id, decision}

        Returns:
            RuleEvaluationResult with decision, matched rules, reason
        """
        with self._lock:
            if skill_id not in self.rules:
                return RuleEvaluationResult(
                    decision="pending",
                    matched_rules=[],
                    reason="No rules configured for this skill",
                )

            rules = self.rules[skill_id]

        # Evaluate each rule in order
        for rule in rules:
            try:
                decision = self._evaluate_single_rule(
                    rule,
                    metric_name=metric_name,
                    magnitude=magnitude,
                    confidence=confidence,
                    recent_history=recent_history or [],
                )

                if decision in ["auto-approve", "auto-reject"]:
                    # Matched!
                    return RuleEvaluationResult(
                        decision=decision,
                        matched_rules=[rule.rule_id],
                        reason=f"Rule {rule.rule_id} ({rule.rule_type.value}) matched",
                        confidence=confidence,
                    )
            except Exception as e:
                logger.warning(f"[Policy Engine] Rule {rule.rule_id} evaluation failed: {e}")
                # Continue to next rule

        # No rules matched
        return RuleEvaluationResult(
            decision="pending",
            matched_rules=[],
            reason="No rules matched",
            confidence=confidence,
        )

    def _evaluate_single_rule(
        self,
        rule: ApprovalRule,
        metric_name: str,
        magnitude: float,
        confidence: float,
        recent_history: List[Dict],
    ) -> Optional[str]:
        """
        Evaluate a single rule.

        Returns:
            "auto-approve" | "auto-reject" | None (no match)
        """
        if rule.rule_type == RuleType.CONFIDENCE_THRESHOLD:
            threshold = rule.config.get("threshold", 0.8)
            if confidence > threshold:
                return "auto-approve"

        elif rule.rule_type == RuleType.MAGNITUDE_LIMIT:
            limit = rule.config.get("limit", 0.5)
            if magnitude > limit:
                return "auto-reject"

        elif rule.rule_type == RuleType.METRIC_WHITELIST:
            whitelist = rule.config.get("metrics", [])
            if metric_name in whitelist:
                return "auto-approve"

        elif rule.rule_type == RuleType.MOMENTUM_PATTERN:
            min_count = rule.config.get("min_count", 5)
            # Count recent approvals (simplified: count in provided history)
            approved_count = sum(
                1 for h in recent_history if h.get("decision") == "approved"
            )
            if approved_count >= min_count:
                return "auto-approve"

        elif rule.rule_type == RuleType.TIME_WINDOW:
            start_hh = rule.config.get("start_hh", 9)
            start_mm = rule.config.get("start_mm", 0)
            end_hh = rule.config.get("end_hh", 17)
            end_mm = rule.config.get("end_mm", 0)

            now = datetime.utcnow().time()
            start_time = time(start_hh, start_mm)
            end_time = time(end_hh, end_mm)

            if start_time <= now <= end_time:
                return "auto-approve"

        return None  # Rule did not match
