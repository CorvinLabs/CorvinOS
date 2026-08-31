"""Integration tests: GuidanceClassifier → MidstreamRouter pipeline.

Tests end-to-end flow from voice input classification to routing.

Week 1-2 integration: GuidanceClassifier → MidstreamRouter

ADR-0280/0281: Voice-Native Midstream Guidance Pipeline
"""

import pytest

from core.voice.guidance import (
    GuidanceClassifier,
    GuidanceEvent,
    GuidanceClass,
    RiskLevel,
)
from core.voice.routing import MidstreamRouter, SubsystemType


class TestEndToEndPipeline:
    """Test complete guidance → classification → routing pipeline."""

    def setup_method(self):
        """Setup classifier and router."""
        self.classifier = GuidanceClassifier()
        self.router = MidstreamRouter()

    @pytest.mark.asyncio
    async def test_model_selection_pipeline(self):
        """Test: voice input "use Opus" → classify → route to CostController."""
        # Step 1: Create voice event
        event = GuidanceEvent(
            id="e2e_001",
            input_text="use Opus for better quality"
        )

        # Step 2: Classify
        classification = await self.classifier.classify(event)
        assert classification.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE
        assert classification.subsystem_hint == "CostController"

        # Step 3: Route
        routing = self.router.route(classification)
        assert routing.primary_target is not None
        assert routing.primary_target.subsystem == SubsystemType.COST_CONTROLLER

    @pytest.mark.asyncio
    async def test_strategy_change_pipeline(self):
        """Test: voice input "try decompose" → classify → route to LoopEngineer."""
        event = GuidanceEvent(
            id="e2e_002",
            input_text="try decompose instead"
        )

        classification = await self.classifier.classify(event)
        assert classification.guidance_class == GuidanceClass.MIDSTREAM_GUIDANCE

        routing = self.router.route(classification)
        assert routing.primary_target is not None
        assert routing.primary_target.subsystem == SubsystemType.LOOP_ENGINEER

    @pytest.mark.asyncio
    async def test_interrupt_pipeline(self):
        """Test: voice input "stop" → classify → route to Orchestrator."""
        event = GuidanceEvent(
            id="e2e_003",
            input_text="stop"
        )

        classification = await self.classifier.classify(event)
        assert classification.guidance_class == GuidanceClass.INTERRUPT

        routing = self.router.route(classification)
        assert routing.primary_target is not None
        assert routing.primary_target.subsystem == SubsystemType.ORCHESTRATOR

    @pytest.mark.asyncio
    async def test_high_risk_pipeline(self):
        """Test: voice input with high-risk guidance routes to SafetyValidator."""
        event = GuidanceEvent(
            id="e2e_004",
            input_text="delete everything"
        )

        classification = await self.classifier.classify(event)
        # Classification should mark as high-risk
        assert classification.risk_level == RiskLevel.HIGH

        routing = self.router.route(classification)
        # High-risk guidance should route to SafetyValidator
        safety_targets = [
            routing.primary_target,
            *routing.alternate_targets
        ]
        has_safety = any(
            t and t.subsystem == SubsystemType.SAFETY_VALIDATOR
            for t in safety_targets
        )
        assert has_safety

    @pytest.mark.asyncio
    async def test_batch_pipeline(self):
        """Test batch processing through full pipeline."""
        events = [
            GuidanceEvent(id="batch_1", input_text="use Opus"),
            GuidanceEvent(id="batch_2", input_text="stop"),
            GuidanceEvent(id="batch_3", input_text="what's next?"),
        ]

        # Classify all
        classifications = await self.classifier.classify_batch(events)

        # Route all
        routings = [self.router.route(c) for c in classifications]

        # Verify all were routed
        assert len(routings) == 3
        assert all(r is not None for r in routings)

        # Verify diversity of targets
        subsystems = {
            r.primary_target.subsystem
            for r in routings
            if r.primary_target
        }
        assert len(subsystems) >= 2  # At least 2 different subsystems


class TestPipelineMetrics:
    """Test metrics collection across pipeline."""

    def setup_method(self):
        """Setup classifier and router."""
        self.classifier = GuidanceClassifier()
        self.router = MidstreamRouter()

    @pytest.mark.asyncio
    async def test_end_to_end_latency(self):
        """Test that total pipeline latency is reasonable."""
        event = GuidanceEvent(id="latency_001", input_text="use Opus")

        classification = await self.classifier.classify(event)
        routing = self.router.route(classification)

        # Total latency should be <500ms (classifier ~300ms + router ~10ms)
        total_latency = classification.latency_ms + routing.latency_ms
        assert total_latency < 500, f"Latency {total_latency}ms exceeds budget"

    @pytest.mark.asyncio
    async def test_metrics_integration(self):
        """Test that metrics are available at each stage."""
        event = GuidanceEvent(id="metrics_001", input_text="use Opus")

        classification = await self.classifier.classify(event)
        routing = self.router.route(classification)

        # Get metrics from both subsystems
        classifier_metrics = self.classifier.get_metrics()
        router_metrics = self.router.get_metrics()

        assert "heuristic_metrics" in classifier_metrics or "llm_metrics" in classifier_metrics
        assert "total_routings" in router_metrics
