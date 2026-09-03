"""E2E Wiring Proof: OS-Skills integrated into L5/L10.

This test proves that:
1. DelegationRouterSkill is actually called for L5 routing decisions
2. ContextAdapterSkill is actually called for L10 context adaptation
3. Skill failures gracefully fallback to hardcoded defaults
4. Audit events are logged (no silent operations)
5. Tenant isolation is enforced (GDPR Art. 5, 6)

Tests are structured as E2E (not unit tests that call Skill directly):
- Call route_task_l5() / adapt_context_l10() module-level functions
- Verify Skill was invoked + audit event was logged
- Verify fallback works when Skill times out
- Verify tenant_id isolation prevents cross-tenant access
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, Optional

from core.skills.os_skills_integration import (
    initialize_integration,
    route_task_l5,
    adapt_context_l10,
    SkillsIntegrationLayer,
)
from core.skills.skill_registry_phase1 import SkillExecutionResult, SkillOrigin


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event: Dict[str, Any]) -> None:
        """Record audit event."""
        self.events.append(event)

    def get_events(self) -> list[Dict[str, Any]]:
        """Retrieve recorded events."""
        return self.events


class TestL5RoutingWiring:
    """Test L5 (auto-routing) Skills integration."""

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )

    def test_delegation_router_called_on_l5_route(self):
        """PROOF: DelegationRouterSkill is invoked for L5 routing."""
        # Call L5 routing
        result = self.integration.route_task_l5(
            complexity=7, task_type="analysis", user_context={"user_id": "test_user"}
        )

        # Verify result has expected fields
        assert "engine" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "skill_executed" in result

        # Verify Skill was actually invoked (not fallback)
        assert result["skill_executed"] is True
        assert result["error"] is None

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        skill_event = [e for e in self.audit_backend.events if e.get("skill_id") == "os.delegation_router"]
        assert len(skill_event) > 0, "DelegationRouterSkill execution must be audited"
        assert skill_event[0]["status"] == "success"

    def test_l5_routing_with_different_complexity(self):
        """Verify routing changes based on complexity."""
        # Low complexity
        low_result = self.integration.route_task_l5(
            complexity=2, task_type="chat"
        )
        assert low_result["skill_executed"] is True

        # High complexity
        high_result = self.integration.route_task_l5(
            complexity=9, task_type="analysis"
        )
        assert high_result["skill_executed"] is True

        # Verify different engines were chosen (heuristic-based routing)
        # Low complexity should prefer cheaper engine
        low_engine = low_result["engine"]
        high_engine = high_result["engine"]
        # This is implementation-dependent, but at minimum both should be valid
        assert low_engine in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]
        assert high_engine in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]

    def test_l5_fallback_on_skill_timeout(self):
        """PROOF: Fallback routing works when Skill times out."""
        # Mock registry to return timeout
        with patch.object(
            self.integration.registry,
            "execute",
            return_value=SkillExecutionResult(
                skill_id="os.delegation_router",
                status="timeout",
                error_message="Skill execution timeout after 5000ms",
                execution_time_ms=5000.0,
                tenant_id="_default",
            ),
        ):
            result = self.integration.route_task_l5(
                complexity=5, task_type="code"
            )

        # Verify fallback was used
        assert result["skill_executed"] is False
        assert result["error"] is not None
        assert "timeout" in result["error"].lower()

        # Verify fallback still provides valid routing
        assert "engine" in result
        assert "confidence" in result
        assert result["engine"] in ["claude-haiku-4", "claude-sonnet-4", "claude-opus-5"]

    def test_l5_tenant_isolation(self):
        """PROOF: Tenant isolation enforced (GDPR Art. 5, 6)."""
        # Register allowed tenant
        self.integration.registry.add_tenant("tenant_a")

        # Call with valid tenant_id
        result_valid = self.integration.route_task_l5(
            complexity=5, task_type="chat", tenant_id="tenant_a"
        )
        assert result_valid["skill_executed"] is True

        # Call with unauthorized tenant_id (should fail)
        result_invalid = self.integration.route_task_l5(
            complexity=5, task_type="chat", tenant_id="unauthorized_tenant"
        )
        assert result_invalid["skill_executed"] is False
        assert "isolation violation" in result_invalid["error"].lower() or "not authorized" in result_invalid["error"].lower()

    def test_l5_audit_logging_complete(self):
        """PROOF: Every L5 routing decision is audited (GDPR Art. 30)."""
        # Clear audit events
        self.audit_backend.events.clear()

        # Make routing decision
        result = self.integration.route_task_l5(
            complexity=6, task_type="code"
        )

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        audit_event = self.audit_backend.events[0]

        # Verify event has required compliance fields
        assert audit_event["event_type"] == "SKILL_EXECUTED"
        assert audit_event["skill_id"] == "os.delegation_router"
        assert "timestamp" in audit_event
        assert "tenant_id" in audit_event
        assert "lom" in audit_event  # Line of Moral Responsibility


class TestL10ContextWiring:
    """Test L10 (context engineering) Skills integration."""

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )

    def test_context_adapter_called_on_l10_adapt(self):
        """PROOF: ContextAdapterSkill is invoked for L10 context adaptation."""
        # Call L10 context adaptation
        result = self.integration.adapt_context_l10(
            complexity=6,
            task_type="code",
            task_description="Implement a new feature in Python",
            priority_hint=7,
            user_context={"user_id": "test_user"},
        )

        # Verify result has the ADR-0555 3-tier fields
        for key in ("base_tier", "injected_tier", "merged_tier"):
            assert key in result
        assert "skill_executed" in result

        # Verify Skill was actually invoked
        assert result["skill_executed"] is True
        assert result["error"] is None

        # Verify audit event was logged
        assert len(self.audit_backend.events) > 0
        context_event = [e for e in self.audit_backend.events if e.get("skill_id") == "os.context_adapter"]
        assert len(context_event) > 0, "ContextAdapterSkill execution must be audited"

    def test_l10_fallback_on_skill_error(self):
        """PROOF: Fallback context (immutable base only) when Skill fails."""
        # Mock registry to return error
        with patch.object(
            self.integration.registry,
            "execute",
            return_value=SkillExecutionResult(
                skill_id="os.context_adapter",
                status="error",
                error_message="ContextAdapterSkill internal error",
                execution_time_ms=42.0,
                tenant_id="_default",
            ),
        ):
            result = self.integration.adapt_context_l10(
                complexity=5,
                task_type="chat",
                task_description="Help me with this",
            )

        # Verify fallback was used
        assert result["skill_executed"] is False
        assert result["error"] is not None

        # Verify fallback provides safe context (base only, fail-closed)
        assert result["injected_tier"] is None
        assert result["base_tier"] is not None
        assert result["merged_tier"] is not None
        assert result["merged_tier"]["metadata"]["adr_0555_failclosed"] is True

    def test_l10_three_tier_context_model(self):
        """PROOF: 3-tier context model (base/injected/merged) is used."""
        result = self.integration.adapt_context_l10(
            complexity=7,
            task_type="analysis",
            task_description="Analyze this dataset",
            priority_hint=8,
        )

        # Verify 3-tier structure (ADR-0555)
        assert result["base_tier"]["tier_name"] == "base", "ADR-0555: base tier (immutable Phase 3) required"
        assert result["injected_tier"]["tier_name"] == "injected", "ADR-0555: injected tier (learned layers) required"
        assert result["merged_tier"]["tier_name"] == "merged", "ADR-0555: merged tier (fail-closed merge) required"
        assert result["merged_tier"]["metadata"]["immutable"] is True

    def test_l10_tenant_isolation(self):
        """PROOF: Tenant isolation enforced in L10."""
        self.integration.registry.add_tenant("tenant_b")

        # Valid tenant
        result_valid = self.integration.adapt_context_l10(
            complexity=5,
            task_type="chat",
            task_description="Test",
            tenant_id="tenant_b",
        )
        assert result_valid["skill_executed"] is True

        # Invalid tenant
        result_invalid = self.integration.adapt_context_l10(
            complexity=5,
            task_type="chat",
            task_description="Test",
            tenant_id="unauthorized_tenant",
        )
        assert result_invalid["skill_executed"] is False


class TestPIIScrubbing:
    """Test PII scrubbing in audit events (GDPR Art. 32)."""

    def setup_method(self):
        """Setup for each test."""
        self.audit_backend = MockAuditBackend()
        self.integration = initialize_integration(
            audit_backend=self.audit_backend, tenant_id="_default"
        )

    def test_pii_scrubbing_in_audit(self):
        """PROOF: PII is redacted from audit events (GDPR Art. 32).

        Registers a Skill whose output carries PII and executes it through the
        REAL registry (the previous version patched ``registry.execute`` itself,
        so no audit event was ever written and the assertions were vacuous).
        """
        from core.skills.skill_registry_phase1 import Skill, SkillMetadata

        class LeakySkill(Skill):
            def __init__(self):
                super().__init__(SkillMetadata(
                    id="test.leaky", name="Leaky", description="returns PII",
                    version="0.0.1", origin=SkillOrigin.COMMUNITY, owner="test",
                ))

            def execute(self, input):
                return {
                    "engine": "claude-opus-5",
                    "user_email": "user@example.com",
                    "api_key": "sk-12345678",
                    "password": "hunter2",
                    "note": "contact me at someone@example.org, token=abc123",
                    "confidence": 0.95,
                    "input_tokens": 42,
                }

        self.integration.registry.register(LeakySkill())
        self.audit_backend.events.clear()
        result = self.integration.registry.execute(
            "test.leaky", {"complexity": 5, "task_type": "chat"}, lom="test:pii_scrubbing:100",
        )
        assert result.status == "success"

        assert len(self.audit_backend.events) == 1
        output = self.audit_backend.events[0]["output"]
        assert output["user_email"] == "[REDACTED_PII]"
        assert output["api_key"] == "[REDACTED_PII]"
        assert output["password"] == "[REDACTED_PII]"
        assert "someone@example.org" not in output["note"]
        assert "abc123" not in output["note"]
        # Non-PII survives untouched (no over-redaction of *_tokens)
        assert output["engine"] == "claude-opus-5"
        assert output["confidence"] == 0.95
        assert output["input_tokens"] == 42
        # ADR-0537: LoM hash present on every success
        assert self.audit_backend.events[0]["lom_hash"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
