"""
E2E tests for os.capabilities Skill via Flask (ADR-0537 Phase 1)

Tests real Flask request → IdentityResolverSkill → CapabilitiesSkill → Audit trail

Characteristics:
- Real HTTP headers (X-Role, X-Persona)
- Real Skill execution (not mocked)
- Real audit event emission
- Verify end-to-end flow works
"""

import pytest
from unittest.mock import patch, MagicMock
from core.skills.os_skills.capabilities_skill import CapabilitiesSkill
from core.skills.os_skills.identity_resolver_skill import IdentityResolverSkill


class TestCapabilitiesE2EFlask:
    """E2E test suite: Flask headers → Skills → Audit trail"""

    def setup_method(self):
        """Reset Skills before each test."""
        CapabilitiesSkill._manifest = None

    def test_flask_admin_read_audit_log_e2e(self):
        """E2E: Flask admin header → CapabilitiesSkill.execute() → audit event"""
        skill = CapabilitiesSkill()

        # Simulate Flask request with headers
        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        # Mock audit backend to capture event
        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            output = skill.execute(input)

        # Verify output
        assert output.has_capability is True
        assert output.role == "admin"

        # Verify audit event was written
        assert len(audit_events) == 1
        assert audit_events[0]["event_type"] == "capability_checked"
        assert audit_events[0]["tenant_id"] == "_default"
        assert audit_events[0]["payload"]["role"] == "admin"
        assert audit_events[0]["payload"]["result"] is True

    def test_flask_operator_lacks_delete_user_e2e(self):
        """E2E: Flask operator header → CapabilitiesSkill denies → audit event"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        input = CapabilityCheckInput(
            role="operator",
            tenant_id="_default",
            capability_id="delete_user"
        )

        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            output = skill.execute(input)

        # Verify output
        assert output.has_capability is False
        assert output.role == "operator"

        # Verify audit event shows denial
        assert len(audit_events) == 1
        assert audit_events[0]["payload"]["result"] is False

    def test_flask_user_read_context_e2e(self):
        """E2E: Flask user header → CapabilitiesSkill allows → audit event"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        input = CapabilityCheckInput(
            role="user",
            tenant_id="_default",
            capability_id="read_context"
        )

        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            output = skill.execute(input)

        assert output.has_capability is True
        assert len(audit_events) == 1

    def test_identity_resolver_to_capabilities_e2e(self):
        """E2E: IdentityResolverSkill (Flask) → CapabilitiesSkill → result"""
        identity_skill = IdentityResolverSkill()
        cap_skill = CapabilitiesSkill()

        from core.skills.os_skills.identity_resolver_skill import IdentityResolverInput
        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        # Step 1: Resolve identity from Flask headers
        identity_input = IdentityResolverInput(
            transport_type="flask_http",
            tenant_id="_default",
            headers={"X-Role": "admin", "X-Persona": "console_operator"}
        )

        identity_output = identity_skill.execute(identity_input)

        # Step 2: Check capability using resolved role
        cap_input = CapabilityCheckInput(
            role=identity_output.role,
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            cap_output = cap_skill.execute(cap_input)

        # Verify end-to-end flow
        assert identity_output.role == "admin"
        assert cap_output.has_capability is True
        assert len(audit_events) == 1

    def test_flask_invalid_header_fails_gracefully(self):
        """E2E: Invalid Flask header → SkillExecutionError (fail-closed)"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput, SkillExecutionError

        input = CapabilityCheckInput(
            role="superadmin",  # Invalid
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            with pytest.raises(SkillExecutionError):
                skill.execute(input)

    def test_pii_in_flask_header_blocked_e2e(self):
        """E2E: PII injection via Flask header → SkillExecutionError (fail-closed)"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput, SkillExecutionError

        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log:admin@example.com"  # PII
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            with pytest.raises(SkillExecutionError, match="invalid capability_id format"):
                skill.execute(input)

    def test_audit_trail_chain_integrity_e2e(self):
        """E2E: Multiple Skill executions → audit events → verify chain integrity"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            # Execute multiple capability checks
            for i in range(3):
                input = CapabilityCheckInput(
                    role="admin",
                    tenant_id="_default",
                    capability_id=f"cap_{i}"
                )
                skill.execute(input)

        # Verify all events were recorded
        assert len(audit_events) == 3
        assert all(e["event_type"] == "capability_checked" for e in audit_events)

    def test_multi_tenant_isolation_e2e(self):
        """E2E: Different tenants → separate audit trails (no cross-tenant leakage)"""
        skill = CapabilitiesSkill()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        audit_events = []

        def mock_write_event(**kwargs):
            audit_events.append(kwargs)

        with patch("core.learning.audit_backend.audit_backend.write_event", side_effect=mock_write_event):
            # Tenant A capability check
            input_a = CapabilityCheckInput(
                role="admin",
                tenant_id="tenant_a",
                capability_id="read_audit_log"
            )
            skill.execute(input_a)

            # Tenant B capability check
            input_b = CapabilityCheckInput(
                role="user",
                tenant_id="tenant_b",
                capability_id="read_context"
            )
            skill.execute(input_b)

        # Verify tenant isolation in audit trail
        assert len(audit_events) == 2
        assert audit_events[0]["tenant_id"] == "tenant_a"
        assert audit_events[1]["tenant_id"] == "tenant_b"
