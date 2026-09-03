"""MidstreamRouter subsystem for routing guidance to subsystems.

Routes classified guidance from GuidanceClassifier to appropriate subsystems:
- CostController: model selection, budget changes
- LoopEngineer: strategy changes, decompose/pivot
- SafetyValidator: high-risk guidance requiring confirmation
- Orchestrator: task queue/priority changes

Handles conflict resolution when multiple guidance targets conflict.

ADR-0281: Voice-Native Midstream Guidance Router
"""

import logging
from datetime import datetime
from typing import Optional

from core.voice.guidance import ClassificationResult, GuidanceClass, RiskLevel

from .router_types import (
    SubsystemType,
    RoutingTarget,
    RoutingConflict,
    RoutingResult,
    RouterMetrics,
    RoutingPriority,
    ConflictResolution,
)

logger = logging.getLogger(__name__)


class MidstreamRouter:
    """Routes classified guidance to appropriate subsystems."""

    def __init__(self):
        """Initialize router."""
        self.metrics = RouterMetrics()
        self.name = "midstream_router"

    def route(self, classification: ClassificationResult) -> RoutingResult:
        """Route a classified guidance to appropriate subsystem.

        Args:
            classification: ClassificationResult from GuidanceClassifier

        Returns:
            RoutingResult with routing target(s) and any conflicts
        """
        import time

        start_time = time.time()

        # Map classification to routing targets
        targets = self._classify_to_targets(classification)

        # Handle routing logic
        result = RoutingResult(
            event_id=classification.event_id,
            guidance_class=classification.guidance_class.value,
            primary_target=None,  # assigned below once targets are resolved
        )

        if not targets:
            # No routing needed for this classification
            result.latency_ms = (time.time() - start_time) * 1000
            logger.info(f"No routing target for {classification.guidance_class.value}")
            return result

        # Check for conflicts
        conflicts = self._detect_conflicts(targets)

        if conflicts:
            result.conflicts = conflicts
            # Resolve conflicts
            resolved = self._resolve_conflicts(conflicts, classification)
            result.primary_target = resolved
        else:
            # Single target, apply directly
            result.primary_target = self._select_primary_target(targets)

        # Set alternate targets
        result.alternate_targets = [t for t in targets if t != result.primary_target]

        # Record metrics
        if result.primary_target:
            self.metrics.record_routing(
                result.primary_target, has_conflict=len(conflicts) > 0
            )

        result.latency_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Routed {classification.event_id} to {result.primary_target.subsystem.value if result.primary_target else 'none'}"
        )

        return result

    def _classify_to_targets(
        self, classification: ClassificationResult
    ) -> list[RoutingTarget]:
        """Map classification to routing targets."""
        targets = []

        if classification.guidance_class == GuidanceClass.INTERRUPT:
            # Interrupt → Orchestrator (stop/pause/cancel)
            targets.append(
                RoutingTarget(
                    subsystem=SubsystemType.ORCHESTRATOR,
                    action="abort_task",
                    priority=RoutingPriority.CRITICAL,
                    requires_confirmation=False,
                )
            )

        elif classification.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE:
            # Midstream guidance → use subsystem hint
            if classification.subsystem_hint == "CostController":
                targets.append(
                    RoutingTarget(
                        subsystem=SubsystemType.COST_CONTROLLER,
                        action="switch_model",
                        priority=RoutingPriority.HIGH,
                        estimated_cost=5.0,
                        metadata={
                            "keywords": classification.matched_keywords,
                        },
                    )
                )
            elif classification.subsystem_hint == "LoopEngineer":
                targets.append(
                    RoutingTarget(
                        subsystem=SubsystemType.LOOP_ENGINEER,
                        action="change_strategy",
                        priority=RoutingPriority.HIGH,
                        metadata={
                            "keywords": classification.matched_keywords,
                        },
                    )
                )
            elif classification.subsystem_hint == "SafetyValidator":
                targets.append(
                    RoutingTarget(
                        subsystem=SubsystemType.SAFETY_VALIDATOR,
                        action="validate_high_risk",
                        priority=RoutingPriority.CRITICAL,
                        requires_confirmation=True,
                        metadata={
                            "keywords": classification.matched_keywords,
                            "risk_level": classification.risk_level.value,
                        },
                    )
                )
            elif classification.subsystem_hint == "Orchestrator":
                targets.append(
                    RoutingTarget(
                        subsystem=SubsystemType.ORCHESTRATOR,
                        action="reorder_queue",
                        priority=RoutingPriority.NORMAL,
                        metadata={
                            "keywords": classification.matched_keywords,
                        },
                    )
                )

            # High-risk guidance always requires SafetyValidator gate
            if (
                classification.risk_level == RiskLevel.HIGH
                and not any(
                    t.subsystem == SubsystemType.SAFETY_VALIDATOR for t in targets
                )
            ):
                targets.append(
                    RoutingTarget(
                        subsystem=SubsystemType.SAFETY_VALIDATOR,
                        action="confirm_high_risk",
                        priority=RoutingPriority.CRITICAL,
                        requires_confirmation=True,
                    )
                )

        elif classification.guidance_class == GuidanceClass.TASK_QUESTION:
            # Task question → StrategyAdvisor
            targets.append(
                RoutingTarget(
                    subsystem=SubsystemType.STRATEGY_ADVISOR,
                    action="answer_question",
                    priority=RoutingPriority.NORMAL,
                    metadata={
                        "keywords": classification.matched_keywords,
                    },
                )
            )

        # TASK_INPUT → no routing needed
        return targets

    def _detect_conflicts(self, targets: list[RoutingTarget]) -> list[RoutingConflict]:
        """Detect conflicts between routing targets."""
        conflicts = []

        # Check for same-subsystem conflicts
        subsys_groups = {}
        for target in targets:
            subsys_name = target.subsystem.value
            if subsys_name not in subsys_groups:
                subsys_groups[subsys_name] = []
            subsys_groups[subsys_name].append(target)

        for subsys_name, subsys_targets in subsys_groups.items():
            if len(subsys_targets) > 1:
                # Multiple targets for same subsystem
                conflicts.append(
                    RoutingConflict(
                        targets=subsys_targets,
                        reason=f"Multiple targets for {subsys_name}",
                        resolution_strategy=ConflictResolution.ESCALATE,
                    )
                )

        # Check for incompatible action conflicts
        action_pairs = {
            ("switch_model", "switch_model"): False,  # Compatible
            ("abort_task", "switch_model"): True,  # Incompatible (abort supersedes model change)
        }

        actions = [(t.subsystem.value, t.action) for t in targets]
        for i, (subsys1, action1) in enumerate(actions):
            for subsys2, action2 in actions[i + 1 :]:
                key = tuple(sorted([action1, action2]))
                if key in action_pairs and action_pairs[key]:
                    conflicts.append(
                        RoutingConflict(
                            targets=[targets[i], targets[i + 1]],
                            reason=f"Incompatible actions: {action1} vs {action2}",
                            resolution_strategy=ConflictResolution.ESCALATE,
                        )
                    )

        return conflicts

    def _resolve_conflicts(
        self,
        conflicts: list[RoutingConflict],
        classification: ClassificationResult,
    ) -> Optional[RoutingTarget]:
        """Resolve conflicts and return primary routing target."""
        if not conflicts:
            return None

        # For now, use escalation (defer to SafetyValidator)
        for conflict in conflicts:
            if len(conflict.targets) > 0:
                # Return highest-priority conflicting target for validation
                highest_priority = max(
                    conflict.targets, key=lambda t: t.priority.value
                )
                highest_priority.requires_confirmation = True
                return highest_priority

        return None

    def _select_primary_target(self, targets: list[RoutingTarget]) -> Optional[RoutingTarget]:
        """Select primary target from list (highest priority)."""
        if not targets:
            return None

        # Sort by priority (descending)
        sorted_targets = sorted(targets, key=lambda t: t.priority.value, reverse=True)
        return sorted_targets[0]

    def get_metrics(self) -> dict:
        """Return router metrics."""
        return {
            "name": self.name,
            "total_routings": self.metrics.total_routings,
            "by_subsystem": self.metrics.by_subsystem,
            "conflicts_total": self.metrics.conflicts_total,
            "conflicts_resolved": self.metrics.conflicts_resolved,
            "conflicts_escalated": self.metrics.conflicts_escalated,
            "avg_latency_ms": self.metrics.avg_latency_ms,
        }
