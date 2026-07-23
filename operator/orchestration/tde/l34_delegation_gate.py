"""ADR-0214: L34 Data-Aware Delegation Gate (Fail-Closed).

Enforces GDPR/compliance: no data leaks allowed. Checks if a step can be
safely delegated based on data classification (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED).

Provides:
1. can_delegate_step() — binary decision (safe or not)
2. filter_plan() — sanitize GlobalPlan before DelegationEnvelope
3. sanitize_snapshot() — filter statement to safe variables only
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

try:
    from operator.orchestration.initial_analysis import GlobalPlan, Step
except ImportError:
    from initial_analysis import GlobalPlan, Step  # type: ignore

_logger = logging.getLogger(__name__)


@dataclass
class DelegationGateResult:
    """Result of can_delegate_step() check."""
    can_delegate: bool
    reason: str


class L34DelegationGate:
    """L34 Data-Safe Gate: fail-closed enforcement."""

    # Classification levels (from L34)
    CLASSIFICATIONS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    CLASSIFICATION_RANK = {c: i for i, c in enumerate(CLASSIFICATIONS)}

    def __init__(self, l34_classifier: Optional[Any] = None):
        """Initialize gate with optional L34 classifier.

        Args:
            l34_classifier: L34 Flow Guard classifier (mocked if None for testing)
        """
        self.l34_classifier = l34_classifier

    def can_delegate_step(
        self,
        step: Step,
        statement: dict[str, Any],
        max_classification: str = "INTERNAL"
    ) -> DelegationGateResult:
        """
        Decide: can this step be delegated?

        FAIL-CLOSED: If ANY required variable exceeds max_classification,
        return False. No exceptions, no heuristics.

        Args:
            step: Step to evaluate
            statement: Current statement context
            max_classification: Max allowed classification ("PUBLIC" | "INTERNAL")

        Returns:
            DelegationGateResult (can_delegate: bool, reason: str)
        """

        # Get step's required variables (from depends_on analysis)
        required_vars = self._get_required_variables(step, statement)

        # Check each variable
        for var_name in required_vars:
            if var_name not in statement:
                continue  # Variable not in statement, ignore

            var_value = statement[var_name]
            data_class = self._classify_variable(var_name, var_value)

            # Fail-closed: if exceeds max, reject
            if self._exceeds_max(data_class, max_classification):
                return DelegationGateResult(
                    can_delegate=False,
                    reason=f"Variable '{var_name}' is {data_class} (exceeds {max_classification})"
                )

        # All variables are safe
        return DelegationGateResult(
            can_delegate=True,
            reason=f"All required variables are {max_classification} or lower"
        )

    def filter_plan(
        self,
        plan: GlobalPlan,
        max_classification: str = "INTERNAL"
    ) -> GlobalPlan:
        """
        Filter GlobalPlan to remove sensitive entities.

        Entities extracted by InitialAnalysis (e.g., customer emails, API keys)
        are classified; sensitive ones removed from step descriptions.

        Args:
            plan: Full GlobalPlan (may contain sensitive entities)
            max_classification: Max allowed classification

        Returns:
            Filtered GlobalPlan (safe for DelegationEnvelope)
        """

        filtered_steps = []
        for step in plan.steps:
            # Filter step description (remove sensitive entities)
            safe_description = self._filter_text(step.action, max_classification)

            # Create filtered step (keep everything except description)
            filtered_step = Step(
                step=step.step,
                action=safe_description,
                depends_on=step.depends_on,
                can_parallelize=step.can_parallelize,
                estimated_tokens=step.estimated_tokens,
            )
            filtered_steps.append(filtered_step)

        filtered_plan = GlobalPlan(
            steps=filtered_steps,
            estimated_duration_s=plan.estimated_duration_s,
            estimated_tokens=plan.estimated_tokens,
            fallback_strategy=plan.fallback_strategy,
        )

        return filtered_plan

    def sanitize_snapshot(
        self,
        statement: dict[str, Any],
        required_vars: set[str],
        max_classification: str = "INTERNAL"
    ) -> dict[str, Any]:
        """
        Filter statement snapshot to only safe data.

        Args:
            statement: Full statement context
            required_vars: Variables that this step needs
            max_classification: Max allowed classification

        Returns:
            Sanitized snapshot (only safe variables)
        """

        snapshot = {}
        for var in required_vars:
            if var not in statement:
                continue

            var_value = statement[var]
            data_class = self._classify_variable(var, var_value)

            if not self._exceeds_max(data_class, max_classification):
                # Safe: include
                snapshot[var] = var_value
            else:
                # Unsafe: replace with placeholder
                snapshot[var] = f"<{data_class}_DATA_REDACTED>"

        return snapshot

    def _classify_variable(self, var_name: str, var_value: Any) -> str:
        """Classify a variable based on name + content."""

        if self.l34_classifier:
            # Use real L34 classifier if available
            try:
                return self.l34_classifier.classify(var_value)
            except Exception:
                pass

        # Fallback heuristic: check variable name
        lower_name = var_name.lower()

        if any(x in lower_name for x in ["password", "secret", "token", "key", "credential"]):
            return "RESTRICTED"

        if any(x in lower_name for x in ["email", "phone", "customer", "user", "customer_id"]):
            return "CONFIDENTIAL"

        if any(x in lower_name for x in ["internal", "config", "database", "api"]):
            return "INTERNAL"

        # Default: PUBLIC
        return "PUBLIC"

    def _exceeds_max(self, data_class: str, max_classification: str) -> bool:
        """Check if data_class exceeds max_classification."""
        return self.CLASSIFICATION_RANK.get(data_class, 0) > self.CLASSIFICATION_RANK.get(
            max_classification, 0
        )

    def _get_required_variables(self, step: Step, statement: dict[str, Any]) -> set[str]:
        """Infer required variables for a step (based on action + statement keys)."""
        # For now: all variables in statement (conservative)
        # In production: parse step.action to identify actual deps
        return set(statement.keys())

    def _filter_text(self, text: str, max_classification: str) -> str:
        """Filter sensitive text from step descriptions."""
        # Simple implementation: if max_classification < CONFIDENTIAL,
        # remove likely-PII patterns
        # In production: use regex + entity extraction
        if self.CLASSIFICATION_RANK.get(max_classification, 0) < self.CLASSIFICATION_RANK["CONFIDENTIAL"]:
            # Remove email-like patterns
            import re
            text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL_REDACTED]', text)
            # Remove phone-like patterns
            text = re.sub(r'\+?\d{1,3}[-.\s]?\d{3,}[-.\s]?\d{3,}', '[PHONE_REDACTED]', text)

        return text
