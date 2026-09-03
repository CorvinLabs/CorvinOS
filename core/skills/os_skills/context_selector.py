"""ACP Skill: os.context_selector — Autonomously select context injection strategy.

Part of Skills 2.0 Agentic Control Plane (ADR-0532).

This Skill determines:
1. Quality mode: QUALITY_MAX (comprehensive context), BALANCED (moderate), EFFICIENCY_MAX (minimal)
2. Which ADR references to inject
3. Which memory items to include
4. Real-time load adjustment

The Skill is learnable via ADR-0314 feedback loop:
- Heuristic layer: task_type-based rules (95% coverage)
- Learned layer: A/B converged modes per task type (5% coverage)
- Real-time layer: load adjustment (downgrade on P99 > 1500ms)
- User override: escape hatch for explicit control

Integration: RequestPipeline calls this Skill to select context before merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Any
from enum import Enum
import inspect
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

def _get_lom() -> str:
    """Get line of moral responsibility (caller's file:function:line)."""
    frame = inspect.currentframe()
    if frame and frame.f_back:
        caller_frame = frame.f_back
        return f"{caller_frame.f_code.co_filename}:{caller_frame.f_code.co_name}:{caller_frame.f_lineno}"
    return "unknown"

# Audit chain writer (lazy singleton)
from core.skills.skill_audit import emit_skill_audit


class QualityMode(Enum):
    """Quality/latency trade-off modes for context injection."""

    QUALITY_MAX = "QUALITY_MAX"  # Comprehensive context, may be slower
    BALANCED = "BALANCED"  # Moderate context, balanced speed/quality
    EFFICIENCY_MAX = "EFFICIENCY_MAX"  # Minimal context, fastest


@dataclass
class ContextSelectionDecision:
    """Decision made by context selector skill."""

    quality_mode: QualityMode
    selected_adr_ids: List[str] = field(default_factory=list)  # Top-N ADRs to inject
    selected_memory_ids: List[str] = field(default_factory=list)  # Top-N memory items
    confidence: float = 0.5  # 0.0–1.0, calibrated via feedback loop
    reasoning: str = ""  # Why this decision was made
    execution_time_ms: float = 0.0  # How long the skill took
    audit_event: dict = field(default_factory=dict)  # For compliance logging


class ContextSelectorSkill:
    """Skill: Autonomously select which context to inject for LLM requests.

    Implements ACP pattern (ADR-0532): deterministic Python + optional LLM decisions.
    Every decision is auditable and learnable via ADR-0314 feedback loop.
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize skill.

        Args:
            tenant_id: Tenant identifier (for multi-tenancy)
        """
        self.tenant_id = tenant_id
        self.learned_modes = {}  # task_type -> converged quality_mode (from A/B testing)
        self.execution_count = 0
        self.start_time = time.time()

    def execute(
        self,
        task_type: str,
        user_id: str,
        time_budget_ms: int = 1000,
        system_load_p99_ms: Optional[int] = None,
        user_override: Optional[str] = None,
    ) -> ContextSelectionDecision:
        """Execute context selection.

        Args:
            task_type: Type of task ("compliance", "routing", "bulk_classification", etc.)
            user_id: User identifier (for personalization)
            time_budget_ms: Available latency budget
            system_load_p99_ms: Current P99 latency (for load adjustment)
            user_override: User-requested quality mode (escape hatch)

        Returns:
            ContextSelectionDecision with selected mode and context items
        """
        start_ms = time.time() * 1000

        # Layer 1: Heuristic rules (deterministic, 95% coverage)
        mode = self._apply_heuristic_layer(task_type)

        # Layer 2: Learned preferences (A/B converged, 5% coverage if available)
        learned_mode = self._load_learned_mode(task_type, user_id)
        if learned_mode:
            mode = learned_mode
            confidence = 0.75
        else:
            confidence = 0.6  # Heuristic-only

        # Layer 3: Real-time load adjustment
        if system_load_p99_ms and system_load_p99_ms > 1500:
            # System overloaded — downgrade to efficiency
            mode = QualityMode.EFFICIENCY_MAX
            confidence = confidence * 0.9  # Lower confidence on downgrade

        # Layer 4: User override (escape hatch)
        if user_override:
            try:
                mode = QualityMode[user_override]
                confidence = 1.0  # User is explicit
            except KeyError:
                logger.warning(
                    f"Invalid user override: {user_override}, ignoring"
                )

        # Select context items based on mode
        selected_adrs = self._select_adr_items(task_type, mode)
        selected_memory = self._select_memory_items(user_id, mode)

        # Build decision
        execution_time = time.time() * 1000 - start_ms
        decision = ContextSelectionDecision(
            quality_mode=mode,
            selected_adr_ids=selected_adrs,
            selected_memory_ids=selected_memory,
            confidence=min(confidence, 0.99),  # Cap at 0.99
            reasoning=self._build_reasoning(
                task_type, mode, system_load_p99_ms, user_override
            ),
            execution_time_ms=execution_time,
            audit_event=self._build_audit_event(
                task_type, mode, user_id, selected_adrs, selected_memory
            ),
        )

        # Emit audit event (hash-chained)
        self._emit_audit_event(decision, user_id, self.tenant_id)

        self.execution_count += 1
        return decision

    def _apply_heuristic_layer(self, task_type: str) -> QualityMode:
        """Apply deterministic heuristic rules (Layer 1).

        Rules based on task category:
        - Compliance/legal → QUALITY_MAX (need all details)
        - Routing/classification → BALANCED (moderate detail)
        - Bulk processing → EFFICIENCY_MAX (speed critical)
        """
        compliance_tasks = ["compliance", "legal", "gdpr", "audit", "governance"]
        routing_tasks = ["routing", "triage", "classification", "dispatch"]
        bulk_tasks = ["bulk", "batch", "background", "processing"]

        if task_type.lower() in compliance_tasks:
            return QualityMode.QUALITY_MAX
        elif task_type.lower() in routing_tasks:
            return QualityMode.BALANCED
        elif task_type.lower() in bulk_tasks:
            return QualityMode.EFFICIENCY_MAX
        else:
            # Default to BALANCED for unknown task types
            return QualityMode.BALANCED

    def _load_learned_mode(self, task_type: str, user_id: str) -> Optional[QualityMode]:
        """Load learned quality mode for this task type (Layer 2).

        Queries A/B testing convergence data (ADR-0314).
        Returns None if not yet converged (fallback to heuristic).
        """
        # TODO: Query ADR-0319 (Attention Budget) for learned modes
        # This is a stub; Phase 4 learning is in ADR-0314/0319
        if task_type in self.learned_modes:
            return self.learned_modes[task_type]
        return None

    def _select_adr_items(self, task_type: str, mode: QualityMode) -> List[str]:
        """Select ADR IDs to inject based on task type + mode."""
        # Map task types to relevant ADRs
        adr_map = {
            "compliance": [
                "ADR-0555",  # Hybrid Context Model
                "ADR-0556",  # Hybrid Context API
                "ADR-0557",  # Compliance
                "ADR-0232",  # Audit trail
                "ADR-0233",  # Audit compliance
            ],
            "routing": [
                "ADR-0532",  # OS-Skills / Delegation
                "ADR-0314",  # Learning
                "ADR-0533",  # Skill composition
            ],
            "bulk_classification": [
                "ADR-0314",  # Learning
                "ADR-0319",  # Attention budget
            ],
        }

        base_adrs = adr_map.get(task_type, ["ADR-0314"])  # Default to learning ADR

        # Select based on mode
        if mode == QualityMode.QUALITY_MAX:
            return base_adrs  # All ADRs
        elif mode == QualityMode.BALANCED:
            return base_adrs[: len(base_adrs) // 2 + 1]  # Top 50%
        else:  # EFFICIENCY_MAX
            return base_adrs[:1]  # Just the first (most relevant)

    def _select_memory_items(self, user_id: str, mode: QualityMode) -> List[str]:
        """Select memory items to inject based on mode."""
        # TODO: Query ADR-0328 (Session Artifact Memory) for user memory
        if mode == QualityMode.QUALITY_MAX:
            return [f"mem_{user_id}_1", f"mem_{user_id}_2", f"mem_{user_id}_3"]
        elif mode == QualityMode.BALANCED:
            return [f"mem_{user_id}_1"]
        else:  # EFFICIENCY_MAX
            return []

    def _build_reasoning(
        self,
        task_type: str,
        mode: QualityMode,
        system_load_p99_ms: Optional[int],
        user_override: Optional[str],
    ) -> str:
        """Build human-readable reasoning for the decision."""
        parts = []
        parts.append(f"Selected {mode.value} for {task_type}")

        if system_load_p99_ms and system_load_p99_ms > 1500:
            parts.append(f"(downgraded due to system load P99={system_load_p99_ms}ms)")

        if user_override:
            parts.append(f"(user override: {user_override})")

        return " ".join(parts)

    def _build_audit_event(
        self,
        task_type: str,
        mode: QualityMode,
        user_id: str,
        selected_adrs: List[str],
        selected_memory: List[str],
    ) -> dict:
        """Build audit event for compliance logging."""
        return {
            "event_type": "context_selection_made",
            "skill_id": "os.context_selector",
            "user_id": user_id,
            "task_type": task_type,
            "quality_mode": mode.value,
            "selected_adrs": selected_adrs,
            "selected_memory_items": selected_memory,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lom": "context_selector.py:execute",
        }

    @staticmethod
    def _emit_audit_event(
        decision: ContextSelectionDecision, user_id: str, tenant_id: str = "_default"
    ) -> None:
        """Emit audit event (hash-chained, GDPR Art. 30/32)."""
        logger.info(
            f"Context selection: user={user_id}, mode={decision.quality_mode.value}, "
            f"confidence={decision.confidence:.2f}, time={decision.execution_time_ms:.1f}ms, "
            f"adrs={len(decision.selected_adr_ids)}, memory={len(decision.selected_memory_ids)}"
        )

        # Write to the TENANT core audit chain (metadata only — ids, counts,
        # timings; never the selected content). The previous writer targeted a
        # hard-coded ~/.corvin/audit.jsonl that ignored CORVIN_HOME and the
        # tenant, in a record format the chain verifier does not read (D-07).
        emit_skill_audit(
            tenant_id, "skill.executed", tool="os.context_selector",
            details={
                "skill_id": "os.context_selector",
                "status": "success",
                "quality_mode": decision.quality_mode.value,
                "selected_adr_count": len(decision.selected_adr_ids),
                "selected_memory_count": len(decision.selected_memory_ids),
                "confidence": round(float(decision.confidence), 4),
                "latency_ms": round(float(decision.execution_time_ms), 3),
                "lom": decision.audit_event.get("lom", "context_selector.py:execute"),
            },
        )


# Global instance (singleton for ACP)
_global_selector: Optional[ContextSelectorSkill] = None


def get_context_selector(tenant_id: str = "_default") -> ContextSelectorSkill:
    """Get or create global context selector skill."""
    global _global_selector
    if _global_selector is None:
        _global_selector = ContextSelectorSkill(tenant_id)
    return _global_selector
