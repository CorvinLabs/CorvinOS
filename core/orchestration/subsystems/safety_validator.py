"""Safety Validator subsystem: Prevent unsafe actions with audit trail.

Phase C: Tenant-native audit trail via AuditChainWriter.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Subsystem
from core.paths.tenant import tenant_audit_file
from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent

logger = logging.getLogger(__name__)


class SafetyValidator(Subsystem):
    """Validate safety of proposed actions with audit trail.

    Phase C: All audit events written to tenant-scoped audit.jsonl via AuditChainWriter.
    """

    def __init__(
        self,
        context: Optional[Any] = None,
        forbidden_actions: List[str] = None,
        max_retry_attempts: int = 3,
    ):
        """Initialize SafetyValidator.

        Args:
            context: ExecutionContext (Phase C) with tenant_id for tenant-scoped operations
            forbidden_actions: List of forbidden actions
            max_retry_attempts: Max retry attempts before escalation
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"
        self.user_id = getattr(context, 'user_id', None) if context else None

        self.forbidden_actions = forbidden_actions or [
            "rm -rf",
            "sudo",
            "delete_all",
            "drop database",
            ":q!",
        ]
        self.max_retry_attempts = max_retry_attempts
        self.violation_count: Dict[str, int] = {}
        self.consecutive_failures: Dict[str, int] = {}  # ADR-0374: circuit breaker
        self.disabled_strategies: Dict[str, float] = {}  # strategy → cooldown timestamp

        # Phase C: Initialize tenant-scoped audit chain writer
        audit_file = tenant_audit_file(self.tenant_id)
        self.audit_writer = AuditChainWriter(audit_file)

    @property
    def name(self) -> str:
        return "safety_validator"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to action events."""
        self.hub = hub
        hub.subscribe("strategy_applied", self.on_strategy)
        hub.subscribe("strategy_failed", self.on_strategy_failed)  # ADR-0374
        logger.info("SafetyValidator started")

    async def on_strategy_failed(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Track strategy failures for circuit breaker (ADR-0374)."""
        strategy = event_data.get("strategy", "unknown")
        if strategy not in self.consecutive_failures:
            self.consecutive_failures[strategy] = 0
        self.consecutive_failures[strategy] += 1
        if self.consecutive_failures[strategy] >= 5:
            # Disable strategy for 48h (48*3600 seconds)
            import time
            self.disabled_strategies[strategy] = time.time() + 48*3600
            logger.warning(f"Strategy '{strategy}' disabled after 5 failures (48h cooldown)")

    def is_strategy_available(self, strategy: str) -> bool:
        """Check if strategy is available (not in cooldown) (ADR-0374)."""
        import time
        if strategy in self.disabled_strategies:
            if time.time() < self.disabled_strategies[strategy]:
                return False
            else:
                del self.disabled_strategies[strategy]
                self.consecutive_failures[strategy] = 0
        return True

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "strategy_applied":
            await self.on_strategy(event_name, event_data)

    async def on_strategy(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Validate strategy safety and log to audit trail."""
        strategy = event_data.get("strategy", "")
        task_id = event_data.get("task_id", "unknown")

        if not self._is_safe(strategy):
            # Phase C: Log safety violation to tenant-scoped audit trail
            try:
                self.audit_writer.write_event_dict(
                    event_type="safety_violation",
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    details={
                        "task_id": task_id,
                        "strategy": strategy,
                        "reason": "forbidden action detected",
                    },
                    severity="warning",
                )
            except Exception as e:
                logger.error(f"Failed to write audit event: {e}")

            self.publish_event(
                "safety_check_failed",
                {
                    "task_id": task_id,
                    "strategy": strategy,
                    "reason": "forbidden action detected",
                },
            )

            if task_id not in self.violation_count:
                self.violation_count[task_id] = 0

            self.violation_count[task_id] += 1

    def _is_safe(self, action: str) -> bool:
        """Check if action is safe."""
        action_lower = action.lower()

        for forbidden in self.forbidden_actions:
            if forbidden.lower() in action_lower:
                return False

        # Additional checks
        if re.search(r"delete|drop|truncate", action_lower):
            # Check for confirmation
            if "confirm" not in action_lower:
                return False

        return True

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle safety queries."""
        if request_type == "is_safe":
            action = kwargs.get("action", "")
            return self._is_safe(action)

        elif request_type == "check_resource":
            resource = kwargs.get("resource", "")
            # Check if resource is protected
            protected = ["prod", "main", "master", "critical"]
            return not any(p in resource.lower() for p in protected)

        elif request_type == "violation_status":
            task_id = kwargs.get("task_id", "unknown")
            violations = self.violation_count.get(task_id, 0)

            return {
                "violation_count": violations,
                "max_attempts": self.max_retry_attempts,
                "should_escalate": violations >= self.max_retry_attempts,
            }

        elif request_type == "get_forbidden_actions":
            return self.forbidden_actions

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("SafetyValidator shutdown")

    def clear_session_cache(self, session_id: str | None = None) -> None:
        """Clear session-scoped state on session reset.

        Clears violation counts and resets failure tracking for circuit breaker.
        """
        try:
            self.violation_count.clear()
            self.consecutive_failures.clear()
            self.disabled_strategies.clear()
            logger.info("SafetyValidator session state cleared")
        except Exception as e:
            logger.error(f"SafetyValidator clear_session_cache failed: {e}")
