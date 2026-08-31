"""Loop Engineer subsystem: Auto-healing with strategy ladder (ADR-0358).

Maintains strategy state via ContextAPI; records all decisions in audit trail.
Subscribes to context updates to react to budget/model changes.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .base import Subsystem
from core.context_engineering.context_api import ContextAPI
from core.learning.operator_fingerprint import OperatorFingerprint

logger = logging.getLogger(__name__)


class LoopEngineer(Subsystem):
    """Apply healing strategies to fix errors automatically."""

    def __init__(
        self,
        max_retries: int = 5,
        strategy_ladder: List[str] = None,
    ):
        self.max_retries = max_retries
        self.strategy_ladder = strategy_ladder or [
            "direct_fix",
            "pivot_approach",
            "decompose",
            "escalate",
        ]
        self.retry_count: Dict[str, int] = {}
        self.strategy_history: Dict[str, List[Dict[str, Any]]] = {}
        self.context_api: ContextAPI = None
        self.strategy_advisor = None  # Injected from hub during startup

    @property
    def name(self) -> str:
        return "loop_engineer"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Inject ContextAPI and StrategyAdvisor, subscribe to events."""
        self.hub = hub

        # Inject ContextAPI for context access
        self.context_api = ContextAPI(self.name, hub.context_bus)

        # Inject StrategyAdvisor for adaptive strategy selection (E2E wiring)
        try:
            self.strategy_advisor = hub.subsystems.get("strategy_advisor")
            if self.strategy_advisor:
                logger.info("StrategyAdvisor wired for adaptive strategy selection")
            else:
                logger.debug("StrategyAdvisor not found in hub; using static ladder fallback")
        except Exception as e:
            logger.warning(f"Failed to wire StrategyAdvisor: {e}; using static ladder fallback")

        # Subscribe to hub events
        hub.subscribe("error_detected", self.on_error_detected)
        hub.subscribe("strategy_succeeded", self.on_strategy_succeeded)
        hub.subscribe("strategy_failed", self.on_strategy_failed)

        # Subscribe to context updates
        asyncio.create_task(
            self.context_api.subscribe_context_updates(self.on_context_updated)
        )

        logger.info("LoopEngineer started with ContextAPI and adaptive strategy selection")

    async def on_context_updated(self, payload: Dict[str, Any]) -> None:
        """React when context changes (e.g., budget, model updates).

        Args:
            payload: Contains subsystem, updates, context_stack, timestamp
        """
        updates = payload.get("updates", {})
        if "budget_remaining" in updates:
            old_budget, new_budget = updates["budget_remaining"]
            # Log budget changes but don't change strategy
            logger.debug(
                f"Budget changed: {old_budget} -> {new_budget}. "
                "May affect cost_controller strategy selection."
            )
        if "model" in updates:
            old_model, new_model = updates["model"]
            logger.debug(
                f"Model changed: {old_model} -> {new_model}. "
                "Subsystem-specific cost calculations may be affected."
            )

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "error_detected":
            await self._apply_strategy(event_data)
        elif event_name == "strategy_succeeded":
            task_id = event_data.get("task_id")
            if task_id:
                self.retry_count[task_id] = 0
        elif event_name == "strategy_failed":
            await self._escalate_if_needed(event_data)

    async def on_error_detected(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Error detected."""
        await self._apply_strategy(event_data)

    async def on_strategy_succeeded(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Strategy succeeded."""
        task_id = event_data.get("task_id")
        if task_id:
            self.retry_count[task_id] = 0

    async def on_strategy_failed(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Strategy failed."""
        await self._escalate_if_needed(event_data)

    async def _apply_strategy(self, event_data: Dict[str, Any]) -> None:
        """Apply next strategy from adaptive ranking or ladder via ContextAPI.

        Integration: Consult BtwAdvisor for pending /btw guidance (Proposal 1).
        Safety (VIB-005): Only apply guidance to NEXT strategy, never retroactively.
        """
        task_id = event_data.get("task_id", "unknown")
        error = event_data.get("error")

        if task_id not in self.retry_count:
            self.retry_count[task_id] = 0
            self.strategy_history[task_id] = []

        if self.retry_count[task_id] >= self.max_retries:
            self.publish_event(
                "escalation_needed",
                {
                    "task_id": task_id,
                    "error": str(error),
                    "reason": "max retries exceeded",
                },
            )
            # Record escalation decision in audit trail
            try:
                self.context_api.record_decision(
                    decision_type="strategy_escalation",
                    value="escalate",
                    reasoning=f"Max retries ({self.max_retries}) exceeded; error: {str(error)[:100]}",
                    confidence=0.95,
                )
            except RuntimeError:
                # Context not initialized; continue
                logger.debug("Context not initialized; decision not recorded")
            return

        # Check for pending /btw guidance (Proposal 1: BtwAdvisor integration)
        # VIB-005: Only affects NEXT strategy, not current file (non-retroactive)
        # K1-004 Fix: Defensive check that hub is initialized
        btw_guidance = None
        if hasattr(self, 'hub') and self.hub:
            try:
                btw_response = await self.hub.request_from_subsystem(
                    "btw_advisor",
                    "get_pending_guidance",
                    task_id=task_id
                )
                if btw_response and btw_response.get("instruction"):
                    btw_guidance = btw_response["instruction"]
                    logger.info(
                        f"Applying /btw guidance: {btw_guidance.get('guidance_type')} "
                        f"({btw_guidance.get('parsed_value', 'N/A')})"
                    )
            except Exception as e:
                # K1-008 Fix: Non-blocking BtwAdvisor query with proper logging
                logger.debug(f"BtwAdvisor query failed (non-blocking, continuing): {type(e).__name__}: {e}")
        else:
            logger.debug("Hub not initialized; skipping BtwAdvisor guidance check")

        # Try adaptive strategy selection via StrategyAdvisor.get_strategy() (E2E wiring)
        strategy = None
        used_adaptive = False
        used_btw_guidance = False

        # If /btw guidance specifies a strategy, use it (non-blocking override)
        if btw_guidance:
            guidance_type = btw_guidance.get("guidance_type")
            parsed_value = btw_guidance.get("parsed_value")

            # Map /btw guidance to strategy
            # Example: /btw use Opus → affect cost_controller or model_selector (not direct strategy)
            # For now: /btw decompose → force "decompose" strategy
            if guidance_type == "decompose":
                strategy = "decompose"
                used_btw_guidance = True
                logger.info(f"/btw guidance applied: strategy={strategy}")

            elif guidance_type == "skip_phase":
                # /btw skip tests → This is a phase-level control, not a strategy
                # Record for downstream handlers (e.g., test_runner can observe this)
                logger.info(f"/btw guidance received: skip phase={parsed_value}")
                # Don't override strategy; let downstream handle phase skipping

            elif guidance_type == "stop":
                # /btw stop → Stop the task
                strategy = "escalate"  # Treat as escalation/stop
                used_btw_guidance = True
                logger.warning("/btw guidance: STOP received; escalating")

            # Note: USE_MODEL guidance affects model_selector, not strategy; handled elsewhere

        if not strategy and self.strategy_advisor:
            try:
                # Build available strategies with REAL empirical data from StrategyAdvisor
                available_strategies = self.strategy_advisor.build_strategy_options(
                    self.strategy_ladder
                )

                # Get fingerprint from context if available
                fingerprint: Optional[OperatorFingerprint] = None
                try:
                    ctx_state = self.context_api.get_context_state()
                    if ctx_state and hasattr(ctx_state, "operator_fingerprint"):
                        fingerprint = ctx_state.operator_fingerprint
                except Exception as fp_error:
                    # Log fingerprint retrieval failures instead of silently swallowing
                    logger.debug(
                        f"Fingerprint retrieval failed (falling back to None): {fp_error}"
                    )

                # Call adaptive strategy selection (E2E wiring point)
                selected = self.strategy_advisor.get_strategy(
                    available_strategies=available_strategies,
                    fingerprint=fingerprint,
                    task_type="error_recovery",
                )

                if selected:
                    strategy = selected.name
                    used_adaptive = True
                    logger.info(
                        f"Selected strategy '{strategy}' via adaptive ranking "
                        f"(fingerprint_confidence={fingerprint.confidence if fingerprint else 'N/A'})"
                    )
            except Exception as e:
                logger.warning(
                    f"Adaptive strategy selection failed ({type(e).__name__}); "
                    f"falling back to static ladder: {e}"
                )
                used_adaptive = False

        # Fallback to static ladder if adaptive selection AND /btw guidance failed
        if not strategy:
            strategy_idx = min(
                self.retry_count[task_id], len(self.strategy_ladder) - 1
            )
            strategy = self.strategy_ladder[strategy_idx]
            logger.debug(f"Using static strategy ladder: '{strategy}' (index {strategy_idx})")

        # Update strategy in context via ContextAPI
        try:
            self.context_api.update_context(
                strategy=strategy,
                strategy_confidence=0.8 + (0.05 * self.retry_count[task_id]),  # Increase with attempts
            )

            # Record strategy decision in audit trail
            if used_btw_guidance:
                selection_mode = "btw_guidance"
            elif used_adaptive:
                selection_mode = "adaptive"
            else:
                selection_mode = "static_ladder"

            self.context_api.record_decision(
                decision_type="strategy_selection",
                value=strategy,
                reasoning=f"Error: {type(error).__name__ if error else 'unknown'} → {strategy} (attempt {self.retry_count[task_id] + 1}/{self.max_retries}, mode={selection_mode})",
                confidence=0.85,
            )
        except RuntimeError as e:
            logger.warning(f"Context not initialized; strategy not recorded: {e}")

        self.publish_event(
            "strategy_applied",
            {
                "task_id": task_id,
                "strategy": strategy,
                "attempt": self.retry_count[task_id] + 1,
                "error": str(error),
            },
        )

        self.retry_count[task_id] += 1

    async def _escalate_if_needed(self, event_data: Dict[str, Any]) -> None:
        """Escalate if strategy failed."""
        task_id = event_data.get("task_id", "unknown")

        if task_id in self.retry_count and self.retry_count[task_id] >= self.max_retries:
            self.publish_event(
                "escalation_needed",
                {
                    "task_id": task_id,
                    "reason": "all strategies exhausted",
                },
            )

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle strategy queries via ContextAPI."""
        if request_type == "next_strategy":
            task_id = kwargs.get("task_id", "unknown")
            if task_id not in self.retry_count:
                self.retry_count[task_id] = 0

            strategy_idx = min(
                self.retry_count[task_id], len(self.strategy_ladder) - 1
            )

            # Try to read strategy from context if available
            strategy = self.strategy_ladder[strategy_idx]
            try:
                context_strategy = self.context_api.query_context("strategy")
                if context_strategy:
                    strategy = context_strategy
            except RuntimeError:
                # Context not initialized; use local strategy
                pass

            return {
                "strategy": strategy,
                "attempt": self.retry_count[task_id],
                "max_attempts": self.max_retries,
            }

        elif request_type == "retry_status":
            task_id = kwargs.get("task_id", "unknown")
            return {
                "retry_count": self.retry_count.get(task_id, 0),
                "max_retries": self.max_retries,
            }

        elif request_type == "strategy_confidence":
            # Read confidence from context
            try:
                confidence = self.context_api.query_context("strategy_confidence")
                return {"confidence": confidence if confidence is not None else 0.5}
            except RuntimeError:
                return {"confidence": 0.5}

        raise ValueError(f"Unknown request type: {request_type}")

    async def _apply_skill_to_error(
        self, error_type: str, skill_name: str
    ) -> bool:
        """Apply skill to recover from error, publish outcome for closed-loop learning (ADR-0372).

        Args:
            error_type: Error class name (e.g., 'TypeError')
            skill_name: Name of skill to apply

        Returns:
            True if skill application succeeded, False otherwise
        """
        start_time = time.time()
        try:
            logger.info(f"Applying skill '{skill_name}' to error '{error_type}'")

            # TODO: Actual skill execution would go here (currently simulated)
            import random

            success = random.random() < 0.8
            latency_ms = (time.time() - start_time) * 1000
            cost_cents = 5.0

            # Publish skill application event (ADR-0372: closed-loop feedback)
            self.publish_event(
                "skill_applied_to_error",
                {
                    "skill_name": skill_name,
                    "error_type": error_type,
                    "latency_ms": latency_ms,
                    "cost_cents": cost_cents,
                },
            )

            # Publish outcome for skill grading (closes the loop)
            outcome = "success" if success else "failure"
            self.publish_event(
                "skill_outcome_measured",
                {
                    "skill_name": skill_name,
                    "error_type": error_type,
                    "outcome": outcome,
                    "latency_ms": latency_ms,
                    "cost_cents": cost_cents,
                },
            )

            logger.info(
                f"Skill '{skill_name}' outcome: {outcome} (latency={latency_ms:.1f}ms, cost={cost_cents:.1f}¢)"
            )
            return success

        except Exception as e:
            logger.error(f"Skill application failed: {e}")
            self.publish_event(
                "skill_outcome_measured",
                {
                    "skill_name": skill_name,
                    "error_type": error_type,
                    "outcome": "failure",
                    "reason": str(e),
                },
            )
            return False

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("LoopEngineer shutdown")

    def clear_session_cache(self, session_id: str | None = None) -> None:
        """Clear session-scoped state on session reset.

        Clears retry counts and strategy history for the session.
        """
        try:
            self.retry_count.clear()
            self.strategy_history.clear()
            logger.info("LoopEngineer session state cleared")
        except Exception as e:
            logger.error(f"LoopEngineer clear_session_cache failed: {e}")
