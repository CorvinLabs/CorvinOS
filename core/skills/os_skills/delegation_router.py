"""L5 Delegation Router Skill — Route tasks to native/ACS/TDE engine.

Skill ID: os.delegation_router
Version: 1.0.0
Tier: core
Origin: builtin

Architecture (ADR-0532):
- Executes at every delegation decision (before_delegation_decision trigger)
- Input: task shape (big_data vs other), context size, tenant_id, force_delegate flag
- Output: decision (native | acs | tde), confidence, reasoning
- Shadow mode (Phase 1): advisory only; bundled engine stands
- Dual-write mode (Phase 2): real decision used for routing
- Learning loop: feedback signals (latency, cost, quality) → confidence updates

Compliance:
- GDPR Art. 30: All decisions logged to audit chain
- GDPR Art. 32: Immutable audit trail + PII scrubbing
- EU AI Act Art. 50: LoM binding in every execution (ADR-0537)
- ADR-0232/0233: Boot tripwire verification before execution
- Tenant isolation: All I/O filtered by tenant_id

Design Pattern:
1. Pure heuristic rules (complexity, task type, force_delegate)
2. Learned config override (confidence thresholds from feedback loop)
3. Audit logging: decision + bundled engine (for learning agreement tracking)
4. Graceful degradation: never escalates to unavailable engines
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin, SkillTier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingDecision:
    """Immutable routing decision with audit metadata."""
    decision: str  # "native" | "acs" | "tde"
    confidence: float  # 0.0–1.0
    reasoning: str  # why this decision
    bundled_engine: Optional[str] = None  # (shadow mode) bundled engine for comparison
    shadow: bool = False  # whether this is advisory-only (Phase 1)
    learned_config_version: Optional[str] = None  # version of learned config applied
    confidence_threshold: Optional[float] = None  # learned threshold that may have escalated


class DelegationRouterSkill(Skill):
    """Route tasks to appropriate engine based on complexity and shape.

    This Skill replaces hardcoded delegation logic with a learnable decision.

    **Input Schema (validated by manifest):**
    ```
    {
        "complexity": int (1–10, optional, default 5)
        "task_type": str (optional: "chat" | "code" | "analysis" | "data_processing")
        "force_delegate": bool (explicit /delegate command, optional)
        "is_big_data": bool (big-data-shaped work, optional)
        "user_context": dict (optional user/task metadata)
        "tenant_id": str (required, for learned config lookup)
        "shadow": bool (advisory mode for Phase 1, optional)
        "bundled_engine": str (the engine the hardcoded rule chose, optional)
    }
    ```

    **Output Schema (validated by manifest):**
    ```
    {
        "decision": "native" | "acs" | "tde"
        "confidence": float (0.0–1.0)
        "reasoning": str (why this decision was made)
        "learned_config_version": str (optional, if learned config was applied)
        "confidence_threshold": float (optional, if learned threshold gated escalation)
        "bundled_engine": str (optional, in shadow mode)
        "shadow": bool (optional, whether this is advisory-only)
    }
    ```

    **Audit Trail:**
    Every execution emits a `skill.executed` event with:
    - skill_id: "os.delegation_router"
    - status: "success" | "timeout" | "error"
    - decision: {"decision": "acs", "confidence": 0.85, ...} (content-free projection)
    - lom: "core/skills/os_skills/delegation_router.py:DelegationRouterSkill.execute"
    - lom_hash: SHA256(source code at LoM) — immutable binding

    **Learning Loop Integration (ADR-0314):**
    - Feedback sources: turn_completed (latency, cost, quality), user_feedback (thumbs, eval)
    - Metrics: latency_actual vs predicted, cost_per_token, quality_outcome
    - Scoring rule: mean_decision_error < 5% (for escalation/deescalation)
    - Learned config (confidence_threshold) read from SkillAdapter on every execution
    - If confidence < learned_threshold → escalate engine tier (haiku→sonnet→opus)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.delegation_router",
            name="Delegation Router",
            description="Route tasks to native/ACS/TDE engine based on complexity and learned thresholds",
            version="1.0.0",
            origin=SkillOrigin.BUILTIN,
            tier=SkillTier.CORE,
            owner="corvin-os-team",
            tags=["routing", "delegation", "os-core", "l5"],
        )
        super().__init__(metadata)
        self._heuristic_rules = _HeuristicRules()
        self._escalation_map = {
            "native": "acs",
            "acs": "tde",
            "tde": None,  # no further escalation
        }

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute routing decision.

        All paths are instrumented for audit + learning feedback.

        Args:
            input: Routing decision input (see schema above)

        Returns:
            Routing decision dict (see schema above)

        Raises:
            Never — all exceptions are caught and logged; returns fallback decision.
        """
        start_time = time.time()

        # Extract + validate input
        tenant_id = input.get("tenant_id", "_default")
        force_delegate = input.get("force_delegate", False)
        is_big_data = input.get("is_big_data", False)
        complexity = input.get("complexity", 5)
        task_type = input.get("task_type", "general")
        user_context = input.get("user_context", {})
        shadow_mode = input.get("shadow", False)
        bundled_engine = input.get("bundled_engine")

        try:
            # Step 1: Apply heuristic rules
            decision, confidence, reasoning = self._heuristic_rules.decide(
                complexity=complexity,
                task_type=task_type,
                force_delegate=force_delegate,
                is_big_data=is_big_data,
            )

            # Step 2: Load learned config + apply confidence threshold
            learned_config = None
            learned_version = None
            threshold = None

            if isinstance(tenant_id, str) and tenant_id:
                try:
                    from .skill_adapter import load_skill_config  # noqa: PLC0415
                    learned_config, learned_version = load_skill_config(
                        "os.delegation_router", tenant_id
                    )
                    threshold = learned_config.confidence_threshold
                except Exception as exc:  # noqa: BLE001
                    # Config read failure is not a routing failure — continue with heuristic
                    logger.debug(
                        "DelegationRouter: learned config unavailable (%s)",
                        type(exc).__name__,
                    )

            # Step 3: Escalate if confidence is below learned threshold
            if threshold is not None and confidence < threshold and decision in self._escalation_map:
                next_decision = self._escalation_map[decision]
                if next_decision is not None:
                    decision = next_decision
                    reasoning = (
                        f"{reasoning}; escalated: confidence {confidence:.2f} < "
                        f"learned threshold {threshold:.2f}"
                    )

            # Step 4: Assemble output (PII-free, audit-ready)
            result = {
                "decision": decision,
                "confidence": confidence,
                "reasoning": reasoning,
            }

            if threshold is not None:
                result["confidence_threshold"] = threshold
                result["learned_config_version"] = learned_version

            if shadow_mode:
                # Advisory execution: record bundled engine for learning agreement tracking
                result["shadow"] = True
                result["bundled_engine"] = bundled_engine

            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "DelegationRouter: complexity=%d, task_type=%s, force_delegate=%s, is_big_data=%s → %s (%.0fms, confidence=%.2f)",
                complexity, task_type, force_delegate, is_big_data, decision, execution_time_ms, confidence,
            )

            return result

        except Exception as exc:  # noqa: BLE001
            # Graceful failure: return conservative heuristic (native)
            logger.exception("DelegationRouter execution failed, falling back to native")
            execution_time_ms = int((time.time() - start_time) * 1000)
            return {
                "decision": "native",
                "confidence": 0.5,  # low confidence on fallback
                "reasoning": f"Router failure ({type(exc).__name__}), conservative fallback to native",
                "error": type(exc).__name__,
            }


class _HeuristicRules:
    """Pure heuristic routing rules (no I/O, no state, no side effects).

    These rules form the baseline decision tree. They are independent of
    learned config (which is applied as an override in DelegationRouterSkill.execute).

    Rules:
    1. Explicit /delegate → acs (user command wins)
    2. Big-data shaped work → acs (fan-out wins on volume)
    3. High complexity (8+) → opus (most capable)
    4. Medium complexity (5–7) → sonnet (balanced)
    5. Low complexity (<5) → haiku (efficient)
    6. Code tasks get boost → try sonnet first
    """

    # Hardcoded engine rankings (never expose to user, immutable)
    _ENGINES = ["haiku", "sonnet", "opus"]
    _ENGINE_CONFIDENCE = {
        "haiku": 0.90,
        "sonnet": 0.85,
        "opus": 0.95,
    }

    def decide(
        self,
        *,
        complexity: int,
        task_type: str,
        force_delegate: bool,
        is_big_data: bool,
    ) -> tuple[str, float, str]:
        """Pure heuristic decision (no I/O, no state).

        Returns:
            (decision, confidence, reasoning) tuple
        """
        # Rule 1: Explicit /delegate command wins
        if force_delegate:
            return "acs", 0.99, "Explicit /delegate command overrides all heuristics"

        # Rule 2: Big-data shaped work → acs
        if is_big_data:
            return "acs", 0.92, "Big-data shaped work routed to ACS for fan-out efficiency"

        # Rule 3–5: Complexity → native (with potential escalation via learned thresholds)
        if complexity >= 8:
            return "native", 0.95, "High complexity task flagged for potential TDE escalation"
        elif complexity >= 5:
            return "native", 0.85, "Medium-high complexity routed natively; learned config may escalate"
        else:
            return "native", 0.90, "Low-medium complexity routed natively (efficient)"

        # Note: Rule 6 (code task boost) is deferred to learned config phase in execute()
