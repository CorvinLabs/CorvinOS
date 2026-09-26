"""Task Governance — Validator Plugin Registry + Approval Orchestration.

Implements hybrid approval workflow:
1. Core approval_state is immutable (metadata column in items table)
2. Pre-decision validators (plugins) are optional, fail-closed
3. Validator decisions are audited separately from approval decisions

Compliance: ADR-0232 (Audit-First), ADR-0233 (Plugin Consolidation), ADR-0156 (Custom Layers)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Immutable validator result."""
    passed: bool
    reason: str
    metadata: dict[str, Any]
    validator_id: str
    validator_version: str


@dataclass(frozen=True)
class ApprovalDecisionPolicy:
    """Policy decision: is approval allowed?"""
    approved: bool
    reason: str
    validators_run: list[str]
    validation_results: dict[str, ValidationResult]
    blocked_by: list[str]  # validator IDs that blocked


class ValidatorInterface:
    """Interface for approval validators (plugins implement this)."""

    def __init__(self, validator_id: str, version: str = "1.0.0"):
        self.validator_id = validator_id
        self.version = version

    async def validate_approval(
        self,
        task_id: str,
        actor: str,
        decision: str,  # 'approve' | 'reject'
    ) -> ValidationResult:
        """
        Validate an approval decision.

        Args:
            task_id: the task being approved
            actor: who is deciding
            decision: 'approve' or 'reject'

        Returns:
            ValidationResult(passed=True|False, reason, metadata)

        Must never raise; return ValidationResult with passed=False instead.
        """
        raise NotImplementedError("Subclasses must implement validate_approval()")


class ValidatorRegistry:
    """Registry of approval validators (CorvinPlugin instances)."""

    def __init__(self):
        self.validators: dict[str, ValidatorInterface] = {}
        self._lock = asyncio.Lock()

    def register(self, validator: ValidatorInterface) -> None:
        """Register a validator."""
        if validator.validator_id in self.validators:
            log.warning(f"Validator {validator.validator_id} already registered; overwriting")
        self.validators[validator.validator_id] = validator
        log.info(f"Validator registered: {validator.validator_id} v{validator.version}")

    def unregister(self, validator_id: str) -> None:
        """Unregister a validator."""
        if validator_id in self.validators:
            del self.validators[validator_id]
            log.info(f"Validator unregistered: {validator_id}")

    async def run_validators(
        self,
        task_id: str,
        actor: str,
        decision: str,
    ) -> dict[str, ValidationResult]:
        """
        Run all registered validators in parallel (with timeout).

        Args:
            task_id: task being approved
            actor: who is deciding
            decision: 'approve' or 'reject'

        Returns:
            {validator_id: ValidationResult, ...}
            Timeouts are caught and converted to ValidationResult(passed=False)
        """
        async with self._lock:
            validators_to_run = list(self.validators.values())

        if not validators_to_run:
            return {}  # No validators registered

        # Run all validators concurrently with 5-second timeout
        results = {}
        tasks = [
            asyncio.wait_for(
                self._run_with_timeout(v, task_id, actor, decision),
                timeout=5.0,
            )
            for v in validators_to_run
        ]

        validator_results = await asyncio.gather(*tasks, return_exceptions=True)
        for v, result in zip(validators_to_run, validator_results):
            if isinstance(result, asyncio.TimeoutError):
                results[v.validator_id] = ValidationResult(
                    passed=False,
                    reason=f"Validator timeout (5s exceeded)",
                    metadata={"timeout": True},
                    validator_id=v.validator_id,
                    validator_version=v.version,
                )
            elif isinstance(result, Exception):
                results[v.validator_id] = ValidationResult(
                    passed=False,
                    reason=f"Validator error: {type(result).__name__}",
                    metadata={"error": str(result)},
                    validator_id=v.validator_id,
                    validator_version=v.version,
                )
            else:
                results[v.validator_id] = result

        return results

    async def _run_with_timeout(
        self,
        validator: ValidatorInterface,
        task_id: str,
        actor: str,
        decision: str,
    ) -> ValidationResult:
        """Run a single validator with exception handling."""
        try:
            return await validator.validate_approval(task_id, actor, decision)
        except Exception as e:  # noqa: BLE001
            log.exception(f"Validator {validator.validator_id} raised exception")
            return ValidationResult(
                passed=False,
                reason=f"Validator exception: {type(e).__name__}: {e}",
                metadata={"exception": str(e)},
                validator_id=validator.validator_id,
                validator_version=validator.version,
            )

    async def check_approval_allowed(
        self,
        task_id: str,
        actor: str,
        decision: str,
    ) -> ApprovalDecisionPolicy:
        """
        Check if approval is allowed (all validators must pass).

        Fails CLOSED: if any validator says "no", approval is blocked.

        Args:
            task_id: task being approved
            actor: who is deciding
            decision: 'approve' or 'reject'

        Returns:
            ApprovalDecisionPolicy with approved=True|False
        """
        validation_results = await self.run_validators(task_id, actor, decision)

        blocked_by = [
            v_id for v_id, result in validation_results.items()
            if not result.passed
        ]

        if blocked_by:
            reasons = [
                f"{v_id}: {validation_results[v_id].reason}"
                for v_id in blocked_by
            ]
            return ApprovalDecisionPolicy(
                approved=False,
                reason=f"Approval blocked by {len(blocked_by)} validator(s): {'; '.join(reasons)}",
                validators_run=list(validation_results.keys()),
                validation_results=validation_results,
                blocked_by=blocked_by,
            )

        return ApprovalDecisionPolicy(
            approved=True,
            reason=f"All {len(validation_results)} validators passed",
            validators_run=list(validation_results.keys()),
            validation_results=validation_results,
            blocked_by=[],
        )


# Global registry instance
_REGISTRY: Optional[ValidatorRegistry] = None


def get_registry() -> ValidatorRegistry:
    """Get or create the global validator registry."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ValidatorRegistry()
    return _REGISTRY


async def initialize_validators(plugin_lifecycle: Optional[Any] = None) -> None:
    """
    Initialize validators from CorvinPlugin lifecycle.

    Args:
        plugin_lifecycle: CorvinPlugin lifecycle object (optional for testing)
    """
    registry = get_registry()
    registry.validators.clear()

    if plugin_lifecycle is None:
        log.info("No plugin lifecycle provided; starting with empty validator registry")
        return

    # TODO: Load validators from plugin_lifecycle
    # For now, this is a stub for future integration with ADR-0233 CorvinPlugin-Lifecycle
    log.info("Validator registry initialized from plugin lifecycle")
