"""
E2E Dual-Log tests: Old CapabilityRegistry vs New Skills (ADR-0537 Phase 1)

Tests that old + new paths return same results (backward compat verification)
Detects divergence between old registry and new Skill
"""

import pytest
from unittest.mock import Mock, patch
from core.legacy_compat.persona_compat import Persona, Role, _CompatCapabilityRegistry


class TestDualLogE2E:
    """E2E test suite: Old registry vs New Skills (divergence detection)"""

    def test_dual_log_admin_read_audit_log_match(self):
        """Dual-log: admin read_audit_log → both paths return True"""
        registry = _CompatCapabilityRegistry()

        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput

        # Mock old registry to return True
        with patch.object(registry, "_query_old_registry", return_value=True):
            # Mock Skill to return True
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = True
                mock_execute.return_value = mock_output

                # Call compat layer (runs both paths)
                result = registry.has_capability(
                    persona=Persona.CONSOLE_OPERATOR,
                    role=Role.ADMIN,
                    capability_id="read_audit_log",
                    tenant_id="_default"
                )

        # Both paths should agree: True
        assert result is True
        assert registry.divergence_count == 0

    def test_dual_log_operator_lacks_delete_user_match(self):
        """Dual-log: operator lacks delete_user → both paths return False"""
        registry = _CompatCapabilityRegistry()

        # Mock old registry to return False
        with patch.object(registry, "_query_old_registry", return_value=False):
            # Mock Skill to return False
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = False
                mock_execute.return_value = mock_output

                result = registry.has_capability(
                    persona=Persona.CONSOLE_OPERATOR,
                    role=Role.OPERATOR,
                    capability_id="delete_user",
                    tenant_id="_default"
                )

        # Both paths should agree: False
        assert result is False
        assert registry.divergence_count == 0

    def test_dual_log_divergence_old_true_new_false(self):
        """Dual-log divergence: old registry True, new Skill False → audit event"""
        registry = _CompatCapabilityRegistry()

        with patch.object(registry, "_query_old_registry", return_value=True):
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = False
                mock_execute.return_value = mock_output

                with patch("core.learning.audit_backend.audit_backend.write_event") as mock_audit:
                    result = registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="read_audit_log",
                        tenant_id="_default"
                    )

        # Old result is returned (Personas still primary in Phase 1)
        assert result is True

        # Divergence should be detected and audited
        assert registry.divergence_count == 1
        assert mock_audit.called
        audit_call = mock_audit.call_args[1]
        assert audit_call["event_type"] == "dual_log_divergence"
        assert audit_call["payload"]["old_registry_result"] is True
        assert audit_call["payload"]["new_skill_result"] is False

    def test_dual_log_divergence_old_false_new_true(self):
        """Dual-log divergence: old registry False, new Skill True → audit event"""
        registry = _CompatCapabilityRegistry()

        with patch.object(registry, "_query_old_registry", return_value=False):
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = True
                mock_execute.return_value = mock_output

                with patch("core.learning.audit_backend.audit_backend.write_event") as mock_audit:
                    result = registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.USER,
                        capability_id="read_context",
                        tenant_id="_default"
                    )

        # Old result is returned (False)
        assert result is False

        # Divergence detected
        assert registry.divergence_count == 1
        assert audit_call["payload"]["old_registry_result"] is False
        assert audit_call["payload"]["new_skill_result"] is True

    def test_dual_log_multiple_checks_no_divergence(self):
        """Dual-log: Multiple checks, all match → divergence_count stays 0"""
        registry = _CompatCapabilityRegistry()

        with patch.object(registry, "_query_old_registry", return_value=True):
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = True
                mock_execute.return_value = mock_output

                with patch("core.learning.audit_backend.audit_backend.write_event"):
                    # Run 5 capability checks
                    for i in range(5):
                        registry.has_capability(
                            persona=Persona.CONSOLE_OPERATOR,
                            role=Role.ADMIN,
                            capability_id=f"cap_{i}",
                            tenant_id="_default"
                        )

        # No divergence should be detected
        assert registry.divergence_count == 0

    def test_dual_log_partial_divergence(self):
        """Dual-log: Some checks match, some diverge → track both"""
        registry = _CompatCapabilityRegistry()

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            # First check: both True (no divergence)
            with patch.object(registry, "_query_old_registry", return_value=True):
                with patch.object(registry.skill, "execute") as mock_execute:
                    mock_output = Mock()
                    mock_output.has_capability = True
                    mock_execute.return_value = mock_output

                    registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="cap_1",
                        tenant_id="_default"
                    )

            # Second check: old True, new False (divergence)
            with patch.object(registry, "_query_old_registry", return_value=True):
                with patch.object(registry.skill, "execute") as mock_execute:
                    mock_output = Mock()
                    mock_output.has_capability = False
                    mock_execute.return_value = mock_output

                    registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="cap_2",
                        tenant_id="_default"
                    )

            # Third check: both False (no divergence)
            with patch.object(registry, "_query_old_registry", return_value=False):
                with patch.object(registry.skill, "execute") as mock_execute:
                    mock_output = Mock()
                    mock_output.has_capability = False
                    mock_execute.return_value = mock_output

                    registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.OPERATOR,
                        capability_id="cap_3",
                        tenant_id="_default"
                    )

        # Only 1 divergence out of 3 checks
        assert registry.divergence_count == 1

    def test_dual_log_multi_tenant_tracking(self):
        """Dual-log: Track divergences per tenant separately"""
        registry = _CompatCapabilityRegistry()

        with patch("core.learning.audit_backend.audit_backend.write_event") as mock_audit:
            # Tenant A: divergence
            with patch.object(registry, "_query_old_registry", return_value=True):
                with patch.object(registry.skill, "execute") as mock_execute:
                    mock_output = Mock()
                    mock_output.has_capability = False
                    mock_execute.return_value = mock_output

                    registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="cap_1",
                        tenant_id="tenant_a"
                    )

            # Tenant B: divergence
            with patch.object(registry, "_query_old_registry", return_value=False):
                with patch.object(registry.skill, "execute") as mock_execute:
                    mock_output = Mock()
                    mock_output.has_capability = True
                    mock_execute.return_value = mock_output

                    registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.USER,
                        capability_id="cap_2",
                        tenant_id="tenant_b"
                    )

        # 2 divergences total
        assert registry.divergence_count == 2

        # Verify audit events record tenant_id
        audit_calls = mock_audit.call_args_list
        assert len(audit_calls) == 2
        assert audit_calls[0][1]["tenant_id"] == "tenant_a"
        assert audit_calls[1][1]["tenant_id"] == "tenant_b"

    def test_dual_log_threshold_alert(self):
        """Dual-log: Alert logged when divergence_count reaches threshold"""
        registry = _CompatCapabilityRegistry()
        registry.divergence_threshold = 2  # Alert after 2 divergences

        with patch("core.learning.audit_backend.audit_backend.write_event"):
            with patch("core.legacy_compat.persona_compat.logger.error") as mock_logger:
                # Trigger 3 divergences (hits threshold on 2nd)
                for i in range(3):
                    with patch.object(registry, "_query_old_registry", return_value=True):
                        with patch.object(registry.skill, "execute") as mock_execute:
                            mock_output = Mock()
                            mock_output.has_capability = False
                            mock_execute.return_value = mock_output

                            registry.has_capability(
                                persona=Persona.CONSOLE_OPERATOR,
                                role=Role.ADMIN,
                                capability_id=f"cap_{i}",
                                tenant_id="_default"
                            )

        # Alert should have been logged when count reached threshold (2)
        # and again at count 3
        assert mock_logger.call_count >= 1
        alert_msg = mock_logger.call_args_list[0][0][0]
        assert "DUAL_LOG_DIVERGENCE ALERT" in alert_msg

    def test_dual_log_old_registry_exception_handled(self):
        """Dual-log: Old registry exception → caught and treated as deny"""
        registry = _CompatCapabilityRegistry()

        # Old registry raises exception
        with patch.object(registry, "_query_old_registry", side_effect=Exception("Registry error")):
            with patch.object(registry.skill, "execute") as mock_execute:
                mock_output = Mock()
                mock_output.has_capability = True
                mock_execute.return_value = mock_output

                with patch("core.learning.audit_backend.audit_backend.write_event"):
                    result = registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="read_audit_log",
                        tenant_id="_default"
                    )

        # Exception should be caught; old result treated as False (deny)
        # New Skill result should be used for divergence check
        assert registry.divergence_count == 1  # Divergence: False vs True
