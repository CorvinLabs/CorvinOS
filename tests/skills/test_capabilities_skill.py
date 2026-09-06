"""
Unit tests for os.capabilities Skill (ADR-0537 Phase 1)

Tests:
- Admin has all capabilities
- Operator has subset
- User has minimal
- Deny-by-default on unknown role
- Deny-by-default on null tenant_id (fail-closed)
- PII injection blocked (capability_id format validation)
- Audit event emitted
- Thread-safe manifest loading
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from core.skills.os_skills.capabilities_skill import (
    CapabilitiesSkill,
    CapabilityCheckInput,
    CapabilityCheckOutput,
    SkillExecutionError,
)


class TestCapabilitiesSkill:
    """Test suite for os.capabilities Skill."""

    def setup_method(self):
        """Reset manifest before each test (thread-safe)."""
        CapabilitiesSkill._manifest = None

    def test_admin_has_read_audit_log(self):
        """Admin role should have read_audit_log capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is True
        assert output.reason == "allowed: admin has read_audit_log"

    def test_admin_has_delete_user(self):
        """Admin role should have delete_user capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="delete_user"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is True

    def test_operator_has_read_audit_log(self):
        """Operator role should have read_audit_log capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="operator",
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is True

    def test_operator_lacks_delete_user(self):
        """Operator role should NOT have delete_user capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="operator",
            tenant_id="_default",
            capability_id="delete_user"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is False
        assert output.reason == "denied: operator lacks delete_user"

    def test_user_has_read_context(self):
        """User role should have read_context capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="user",
            tenant_id="_default",
            capability_id="read_context"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is True

    def test_user_lacks_delete_user(self):
        """User role should NOT have delete_user capability."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="user",
            tenant_id="_default",
            capability_id="delete_user"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            output = skill.execute(input)

        assert output.has_capability is False

    def test_deny_by_default_unknown_role(self):
        """Unknown role should raise SkillExecutionError (fail-closed)."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="superadmin",  # Not in enum
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        with pytest.raises(SkillExecutionError, match="invalid role"):
            skill.execute(input)

    def test_deny_by_default_null_tenant_id(self):
        """Null tenant_id should raise SkillExecutionError (fail-closed)."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id=None,  # type: ignore
            capability_id="read_audit_log"
        )

        with pytest.raises(SkillExecutionError, match="tenant_id required"):
            skill.execute(input)

    def test_deny_by_default_empty_tenant_id(self):
        """Empty tenant_id should raise SkillExecutionError (fail-closed)."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="",
            capability_id="read_audit_log"
        )

        with pytest.raises(SkillExecutionError, match="tenant_id required"):
            skill.execute(input)

    def test_pii_leakage_prevention_email_in_capability_id(self):
        """Capability_id with email (PII) should be rejected."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log:admin@example.com"  # PII injection attempt
        )

        with pytest.raises(SkillExecutionError, match="invalid capability_id format"):
            skill.execute(input)

    def test_pii_leakage_prevention_special_chars(self):
        """Capability_id with special chars should be rejected."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log; DROP TABLE"  # SQL injection attempt
        )

        with pytest.raises(SkillExecutionError, match="invalid capability_id format"):
            skill.execute(input)

    def test_audit_event_emitted_on_success(self):
        """Capability check should emit audit event on success."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        with patch("core.learning.audit_backend.audit_backend.write_event") as mock_write:
            output = skill.execute(input)

        # Verify audit event was written
        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args[1]
        assert call_kwargs["tenant_id"] == "_default"
        assert call_kwargs["event_type"] == "capability_checked"
        assert call_kwargs["payload"]["role"] == "admin"
        assert call_kwargs["payload"]["capability_id"] == "read_audit_log"
        assert call_kwargs["payload"]["result"] is True

    def test_audit_failure_fail_closed(self):
        """If audit write fails, capability check should fail-closed (deny)."""
        skill = CapabilitiesSkill()
        input = CapabilityCheckInput(
            role="admin",
            tenant_id="_default",
            capability_id="read_audit_log"
        )

        # Mock audit backend to raise exception
        with patch("core.learning.audit_backend.audit_backend.write_event") as mock_write:
            mock_write.side_effect = Exception("Audit backend unavailable")

            with pytest.raises(SkillExecutionError, match="Audit trail write failed"):
                skill.execute(input)

    def test_thread_safe_manifest_loading(self):
        """Manifest should be loaded exactly once (thread-safe)."""
        import threading

        results = []

        def init_skill():
            skill = CapabilitiesSkill()
            results.append(skill.capabilities_by_role)

        # Spawn 5 threads that concurrently init Skill
        threads = [threading.Thread(target=init_skill) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should reference the same manifest object
        assert len(results) == 5
        assert all(r is results[0] for r in results), "Manifest should be loaded once (same object)"
