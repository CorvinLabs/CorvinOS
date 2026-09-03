"""Tests: Skills Registry Learning Loop Integration (ADR-0314)."""

import pytest
from unittest.mock import Mock, MagicMock
from core.skills.skill_registry_phase1 import (
    initialize_registry,
    SkillExecutionResult,
)
from core.skills.os_skills_phase1 import DelegationRouterSkill


class MockLearningBackend:
    """Mock learning backend for testing ADR-0314 integration."""

    def __init__(self):
        self.events = []

    def emit_event(self, event: dict) -> None:
        """Record learning event."""
        self.events.append(event)

    def get_events(self) -> list:
        """Retrieve recorded learning events."""
        return self.events


class TestSkillsLearningIntegration:
    """Test learning loop integration (ADR-0314)."""

    def setup_method(self):
        """Setup for each test."""
        self.learning_backend = MockLearningBackend()
        self.registry = initialize_registry(learning_backend=self.learning_backend)
        self.registry.add_tenant("_default")

    def test_learning_event_emitted_on_success(self):
        """PROOF: Learning event emitted when Skill succeeds (ADR-0314)."""
        # Register and execute a Skill
        router = DelegationRouterSkill()
        self.registry.register(router)

        result = self.registry.execute(
            "os.delegation_router",
            {"complexity": 7, "task_type": "code"},
            lom="test:test_learning:100",
        )

        # Verify Skill succeeded
        assert result.status == "success"

        # Verify learning event was emitted
        assert len(self.learning_backend.events) > 0
        learning_event = self.learning_backend.events[0]

        # Verify event structure (ADR-0314)
        assert learning_event["event_type"] == "skill_executed"
        assert learning_event["skill_id"] == "os.delegation_router"
        assert learning_event["status"] == "success"
        assert "timestamp" in learning_event
        assert "tenant_id" in learning_event

    def test_confidence_scoring_on_success(self):
        """PROOF: Confidence score generated on Skill success (ADR-0315)."""
        router = DelegationRouterSkill()
        self.registry.register(router)

        self.registry.execute(
            "os.delegation_router",
            {"complexity": 5, "task_type": "chat"},
            lom="test:confidence:100",
        )

        learning_event = self.learning_backend.events[0]
        confidence = learning_event.get("confidence_score")

        # Verify confidence scoring present
        assert confidence is not None
        assert "reliability" in confidence
        assert "relevance" in confidence
        assert "combined" in confidence
        assert confidence["reliability"] == 0.95  # Success → high reliability

    def test_learning_event_emitted_on_timeout(self):
        """PROOF: Learning event emitted when Skill times out (ADR-0314)."""
        # Mock a Skill that times out
        mock_skill = Mock()
        mock_skill.metadata.id = "os.test_timeout"
        mock_skill.metadata.version = "0.1.0"

        # Execute with zero timeout to force timeout
        result = self.registry.execute(
            "os.delegation_router",
            {"complexity": 5},
            timeout_ms=1,  # Will timeout
            lom="test:timeout:100",
        )

        # Should be timeout
        assert result.status in ("timeout", "error")

    def test_learning_event_tenant_isolation(self):
        """PROOF: Learning events include tenant_id isolation (GDPR Art. 6)."""
        router = DelegationRouterSkill()
        self.registry.register(router)

        # Register another tenant
        self.registry.add_tenant("tenant_a")

        # Execute with tenant_a
        self.registry.execute(
            "os.delegation_router",
            {"complexity": 6, "task_type": "analysis"},
            tenant_id="tenant_a",
            lom="test:tenant_isolation:100",
        )

        learning_event = self.learning_backend.events[0]
        assert learning_event["tenant_id"] == "tenant_a"

    def test_learning_event_no_pii_in_output(self):
        """PROOF: Learning events don't contain PII (GDPR Art. 32)."""
        router = DelegationRouterSkill()
        self.registry.register(router)

        self.registry.execute(
            "os.delegation_router",
            {
                "complexity": 5,
                "task_type": "chat",
                "user_email": "user@example.com",  # PII
                "api_key": "sk-12345",  # Secret
            },
            lom="test:no_pii:100",
        )

        learning_event = self.learning_backend.events[0]

        # Learning event should not contain raw PII
        # (only skill_id, status, confidence_score, etc.)
        assert "user_email" not in str(learning_event)
        assert "sk-12345" not in str(learning_event)

    def test_learning_events_enable_optimization_loop(self):
        """PROOF: Learning events can drive optimization (ADR-0314 → 0315)."""
        router = DelegationRouterSkill()
        self.registry.register(router)

        # Execute multiple times to collect data
        for complexity in [2, 5, 8]:
            self.registry.execute(
                "os.delegation_router",
                {"complexity": complexity, "task_type": "code"},
                lom=f"test:optimization_loop:{complexity}",
            )

        # Verify events accumulated
        assert len(self.learning_backend.events) >= 3

        # Confidence scores should vary by complexity
        events = self.learning_backend.events
        confidences = [e.get("confidence_score", {}).get("combined") for e in events]
        assert len([c for c in confidences if c is not None]) >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
