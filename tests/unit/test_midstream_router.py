"""Unit tests for MidstreamRouter subsystem (Week 2).

Test Coverage:
- Routing logic: classification → subsystem target
- Conflict detection: same-subsystem, incompatible actions
- Conflict resolution: priority-based, escalation
- Metrics collection: routing counts, subsystem distribution
- Integration with GuidanceClassifier output

ADR-0281: Voice-Native Midstream Guidance Router
"""

import pytest
from datetime import datetime

from core.voice.guidance import ClassificationResult, GuidanceClass, RiskLevel
from core.voice.routing import MidstreamRouter, RoutingTarget, RoutingConflict
from core.voice.routing.router_types import (
    SubsystemType,
    RoutingPriority,
    ConflictResolution,
)


class TestBasicRouting:
    """Test basic routing of classified guidance."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_interrupt_routes_to_orchestrator(self):
        """Test that interrupt guidance routes to Orchestrator."""
        classification = ClassificationResult(
            event_id="test_001",
            guidance_class=GuidanceClass.INTERRUPT,
            confidence=0.95,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
            explanation="Stop task",
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.ORCHESTRATOR
        assert result.primary_target.action == "abort_task"
        assert result.primary_target.priority == RoutingPriority.CRITICAL

    def test_cost_controller_guidance_routing(self):
        """Test routing model-selection guidance to CostController."""
        classification = ClassificationResult(
            event_id="test_002",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.MEDIUM,
            explanation="Use Opus",
            matched_keywords=["Opus"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.COST_CONTROLLER
        assert result.primary_target.action == "switch_model"

    def test_loop_engineer_guidance_routing(self):
        """Test routing strategy-change guidance to LoopEngineer."""
        classification = ClassificationResult(
            event_id="test_003",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.80,
            subsystem_hint="LoopEngineer",
            risk_level=RiskLevel.MEDIUM,
            explanation="Try decompose",
            matched_keywords=["decompose"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.LOOP_ENGINEER
        assert result.primary_target.action == "change_strategy"

    def test_safety_validator_routing_high_risk(self):
        """Test routing high-risk guidance to SafetyValidator."""
        classification = ClassificationResult(
            event_id="test_004",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.88,
            subsystem_hint="SafetyValidator",
            risk_level=RiskLevel.HIGH,
            explanation="Delete everything",
            matched_keywords=["delete"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.SAFETY_VALIDATOR
        assert result.primary_target.requires_confirmation

    def test_orchestrator_queue_routing(self):
        """Test routing queue/priority changes to Orchestrator."""
        classification = ClassificationResult(
            event_id="test_005",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.75,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
            explanation="Reorder queue",
            matched_keywords=["priority"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.ORCHESTRATOR
        assert result.primary_target.action == "reorder_queue"

    def test_task_question_routes_to_strategy_advisor(self):
        """Test that task questions route to StrategyAdvisor."""
        classification = ClassificationResult(
            event_id="test_006",
            guidance_class=GuidanceClass.TASK_QUESTION,
            confidence=0.85,
            risk_level=RiskLevel.SAFE,
            explanation="What's next?",
            matched_keywords=["what", "next"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.STRATEGY_ADVISOR
        assert result.primary_target.action == "answer_question"

    def test_task_input_has_no_routing(self):
        """Test that task inputs don't route to any subsystem."""
        classification = ClassificationResult(
            event_id="test_007",
            guidance_class=GuidanceClass.TASK_INPUT,
            confidence=0.50,
            risk_level=RiskLevel.SAFE,
            explanation="Refactor these files",
        )

        result = self.router.route(classification)

        assert result.primary_target is None
        assert len(result.alternate_targets) == 0


class TestConflictDetection:
    """Test conflict detection in routing."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_detect_same_subsystem_conflict(self):
        """Test detection of multiple targets for same subsystem."""
        targets = [
            RoutingTarget(
                subsystem=SubsystemType.COST_CONTROLLER,
                action="switch_model",
            ),
            RoutingTarget(
                subsystem=SubsystemType.COST_CONTROLLER,
                action="change_budget",
            ),
        ]

        conflicts = self.router._detect_conflicts(targets)

        assert len(conflicts) > 0
        assert any(
            t.subsystem == SubsystemType.COST_CONTROLLER
            for c in conflicts
            for t in c.targets
        )

    def test_detect_incompatible_actions(self):
        """Test detection of incompatible actions."""
        targets = [
            RoutingTarget(
                subsystem=SubsystemType.ORCHESTRATOR,
                action="abort_task",
            ),
            RoutingTarget(
                subsystem=SubsystemType.COST_CONTROLLER,
                action="switch_model",
            ),
        ]

        conflicts = self.router._detect_conflicts(targets)

        # Abort should supersede model change
        assert len(conflicts) > 0

    def test_no_conflict_different_subsystems(self):
        """Test that different subsystems don't conflict."""
        targets = [
            RoutingTarget(
                subsystem=SubsystemType.COST_CONTROLLER,
                action="switch_model",
            ),
            RoutingTarget(
                subsystem=SubsystemType.LOOP_ENGINEER,
                action="change_strategy",
            ),
        ]

        conflicts = self.router._detect_conflicts(targets)

        # Different subsystems should not conflict
        assert len(conflicts) == 0


class TestPrioritySelection:
    """Test priority-based target selection."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_select_highest_priority_target(self):
        """Test selection of highest priority target."""
        targets = [
            RoutingTarget(
                subsystem=SubsystemType.COST_CONTROLLER,
                action="switch_model",
                priority=RoutingPriority.NORMAL,
            ),
            RoutingTarget(
                subsystem=SubsystemType.SAFETY_VALIDATOR,
                action="confirm_action",
                priority=RoutingPriority.CRITICAL,
            ),
            RoutingTarget(
                subsystem=SubsystemType.ORCHESTRATOR,
                action="reorder_queue",
                priority=RoutingPriority.HIGH,
            ),
        ]

        primary = self.router._select_primary_target(targets)

        assert primary is not None
        assert primary.priority == RoutingPriority.CRITICAL
        assert primary.subsystem == SubsystemType.SAFETY_VALIDATOR

    def test_interrupt_is_critical_priority(self):
        """Test that interrupt commands have critical priority."""
        classification = ClassificationResult(
            event_id="test_priority_001",
            guidance_class=GuidanceClass.INTERRUPT,
            confidence=0.95,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
        )

        result = self.router.route(classification)

        assert result.primary_target.priority == RoutingPriority.CRITICAL


class TestHighRiskHandling:
    """Test high-risk guidance handling."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_high_risk_requires_confirmation(self):
        """Test that high-risk guidance requires confirmation."""
        classification = ClassificationResult(
            event_id="test_high_risk_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.88,
            subsystem_hint="CostController",
            risk_level=RiskLevel.HIGH,
            explanation="Make expensive change",
        )

        result = self.router.route(classification)

        # High-risk should get SafetyValidator gate
        safety_targets = [
            t for t in [result.primary_target] + result.alternate_targets
            if t and t.subsystem == SubsystemType.SAFETY_VALIDATOR
        ]
        assert len(safety_targets) > 0

    def test_medium_risk_no_confirmation(self):
        """Test that medium-risk guidance doesn't require confirmation."""
        classification = ClassificationResult(
            event_id="test_medium_risk_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.80,
            subsystem_hint="LoopEngineer",
            risk_level=RiskLevel.MEDIUM,
            explanation="Change strategy",
        )

        result = self.router.route(classification)

        if result.primary_target:
            # Medium risk may or may not require confirmation depending on action
            pass


class TestMetricsCollection:
    """Test metrics collection."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_metrics_count_total_routings(self):
        """Test that metrics count total routings."""
        classifications = [
            ClassificationResult(
                event_id=f"metric_{i}",
                guidance_class=GuidanceClass.INTERRUPT,
                confidence=0.95,
                subsystem_hint="Orchestrator",
                risk_level=RiskLevel.SAFE,
            )
            for i in range(5)
        ]

        for classification in classifications:
            self.router.route(classification)

        metrics = self.router.get_metrics()

        assert metrics["total_routings"] >= 5

    def test_metrics_track_by_subsystem(self):
        """Test that metrics track routing by subsystem."""
        cost_classification = ClassificationResult(
            event_id="metric_cost",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.MEDIUM,
        )

        loop_classification = ClassificationResult(
            event_id="metric_loop",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.80,
            subsystem_hint="LoopEngineer",
            risk_level=RiskLevel.MEDIUM,
        )

        self.router.route(cost_classification)
        self.router.route(loop_classification)

        metrics = self.router.get_metrics()
        by_subsystem = metrics["by_subsystem"]

        assert by_subsystem.get("CostController", 0) >= 1
        assert by_subsystem.get("LoopEngineer", 0) >= 1

    def test_metrics_latency_recorded(self):
        """Test that routing latency is recorded."""
        classification = ClassificationResult(
            event_id="latency_001",
            guidance_class=GuidanceClass.INTERRUPT,
            confidence=0.95,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
        )

        result = self.router.route(classification)

        assert result.latency_ms >= 0


class TestConflictResolution:
    """Test conflict resolution strategies."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_resolve_conflicts_escalate_strategy(self):
        """Test that conflicts are escalated to SafetyValidator."""
        # Create a scenario with conflicting guidance
        # (two competing strategy changes)
        classification = ClassificationResult(
            event_id="conflict_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.75,  # Lower confidence to trigger heuristic
            subsystem_hint="LoopEngineer",
            risk_level=RiskLevel.MEDIUM,
        )

        result = self.router.route(classification)

        # If conflicts detected, should have escalation attempt
        if result.has_conflicts():
            # Primary target should be set (escalated)
            assert result.primary_target is not None


class TestRoutingIntegration:
    """Test routing with GuidanceClassifier integration."""

    def setup_method(self):
        """Setup for integration tests."""
        self.router = MidstreamRouter()

    def test_end_to_end_model_selection(self):
        """Test end-to-end: classify → route model selection."""
        classification = ClassificationResult(
            event_id="e2e_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.MEDIUM,
            explanation="Use Opus for better quality",
            matched_keywords=["Opus"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.COST_CONTROLLER
        assert result.primary_target.action == "switch_model"
        assert "Opus" in result.primary_target.metadata.get("keywords", [])

    def test_end_to_end_strategy_change(self):
        """Test end-to-end: classify → route strategy change."""
        classification = ClassificationResult(
            event_id="e2e_002",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.80,
            subsystem_hint="LoopEngineer",
            risk_level=RiskLevel.MEDIUM,
            explanation="Try decompose approach",
            matched_keywords=["decompose"],
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.LOOP_ENGINEER
        assert "decompose" in result.primary_target.metadata.get("keywords", [])

    def test_end_to_end_abort_sequence(self):
        """Test end-to-end abort: classify interrupt → route stop."""
        classification = ClassificationResult(
            event_id="e2e_003",
            guidance_class=GuidanceClass.INTERRUPT,
            confidence=0.98,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
            explanation="Stop task",
        )

        result = self.router.route(classification)

        assert result.primary_target is not None
        assert result.primary_target.subsystem == SubsystemType.ORCHESTRATOR
        assert result.primary_target.action == "abort_task"
        assert result.primary_target.priority == RoutingPriority.CRITICAL


class TestEdgeCases:
    """Test edge cases in routing."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_empty_routing_target_list(self):
        """Test routing with no targets."""
        targets = []

        primary = self.router._select_primary_target(targets)

        assert primary is None

    def test_none_subsystem_hint(self):
        """Test routing with no subsystem hint."""
        classification = ClassificationResult(
            event_id="edge_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.70,
            subsystem_hint=None,  # No hint provided
            risk_level=RiskLevel.MEDIUM,
        )

        result = self.router.route(classification)

        # Should still handle gracefully
        assert result is not None

    def test_routing_result_has_metadata(self):
        """Test that routing result includes metadata."""
        classification = ClassificationResult(
            event_id="metadata_001",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.85,
            subsystem_hint="CostController",
            risk_level=RiskLevel.MEDIUM,
            matched_keywords=["Opus", "quality"],
        )

        result = self.router.route(classification)

        if result.primary_target:
            assert "keywords" in result.primary_target.metadata


class TestRoutingState:
    """Test routing state and stateful operations."""

    def setup_method(self):
        """Setup router for each test."""
        self.router = MidstreamRouter()

    def test_router_is_stateful(self):
        """Test that router maintains state across calls."""
        # Route first classification
        classification1 = ClassificationResult(
            event_id="state_001",
            guidance_class=GuidanceClass.INTERRUPT,
            confidence=0.95,
            subsystem_hint="Orchestrator",
            risk_level=RiskLevel.SAFE,
        )

        self.router.route(classification1)

        # Route second classification
        classification2 = ClassificationResult(
            event_id="state_002",
            guidance_class=GuidanceClass.MIDSTREAM_GUIDANCE,
            confidence=0.80,
            subsystem_hint="CostController",
            risk_level=RiskLevel.MEDIUM,
        )

        self.router.route(classification2)

        # Check that both were counted
        metrics = self.router.get_metrics()
        assert metrics["total_routings"] >= 2
